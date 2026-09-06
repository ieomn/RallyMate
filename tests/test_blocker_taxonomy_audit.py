from __future__ import annotations

import json
import copy
import unittest
from pathlib import Path

from rallymate_scoring.blocker_taxonomy_audit import (
    CONFIDENCE_REPLAY_TOLERANCE,
    BlockerTaxonomyAuditError,
    build_blocker_taxonomy_replay_audit,
    validate_blocker_taxonomy_replay_audit,
)


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "reports" / "measurement-recovery-m78" / "blocker-taxonomy-audit.json"


class BlockerTaxonomyAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = build_blocker_taxonomy_replay_audit(
            taxonomy_path=ROOT / "scoring-blocker-taxonomy.json",
            readiness_audit_path=ROOT
            / "reports"
            / "multivideo-scoring-readiness"
            / "m65-halpe26-three-video-typed-blocker-recovery-v1"
            / "audit.json",
            baseline_runs_root=ROOT
            / "reports"
            / "scoring-candidate-multivideo-m63"
            / "runs",
            replay_runs_root=ROOT
            / "reports"
            / "scoring-candidate-multivideo-m78"
            / "runs",
            audit_id="m78-halpe26-three-video-blocker-taxonomy-v1",
        )

    def test_real_three_video_replay_preserves_score_semantics(self) -> None:
        payload = copy.deepcopy(self.payload)
        validate_blocker_taxonomy_replay_audit(payload)
        self.assertEqual(2366, payload["scope"]["indicator_event_instance_count"])
        self.assertTrue(payload["replay_comparison"]["score_semantics_exact"])
        self.assertLessEqual(
            payload["replay_comparison"]["max_abs_confidence_delta"],
            CONFIDENCE_REPLAY_TOLERANCE,
        )
        self.assertFalse(any(payload["safety"].values()))

    def test_checked_in_audit_replays_exactly_except_timestamp(self) -> None:
        checked_in = json.loads(OUTPUT.read_text(encoding="utf-8"))
        rebuilt = copy.deepcopy(self.payload)
        checked_in.pop("generated_at")
        rebuilt.pop("generated_at")
        self.assertEqual(checked_in, rebuilt)

    def test_invalid_safety_claim_is_rejected(self) -> None:
        payload = copy.deepcopy(self.payload)
        payload["safety"]["score_accuracy_claim"] = True
        with self.assertRaises(BlockerTaxonomyAuditError):
            validate_blocker_taxonomy_replay_audit(payload)


if __name__ == "__main__":
    unittest.main()
