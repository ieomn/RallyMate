from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from rallymate_scoring.run_bundle_binding import (
    ScoringRunBundleBindingError,
    authorize_scoring_run_bundle,
    build_scoring_run_bundle_entry,
    build_trusted_scoring_run_bundle_ledger,
    load_trusted_scoring_run_bundle_ledger,
    scoring_run_bundle_identity,
    scoring_run_bundle_root_sha256,
    validate_trusted_scoring_run_bundle_ledger,
)
from rallymate_vision.pose.metadata import sha256_file


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_NAMES = (
    "events_jsonl",
    "features_jsonl",
    "indicator_features_jsonl",
    "scores_jsonl",
    "event_feature_errors_json",
)


def _summary() -> dict:
    return {
        "schema_version": "1.0.0",
        "loop_version": "minimum-scoring-loop-v0.6.0",
        "video_id": "video-001",
        "model_versions": {"pose": "synthetic-pose"},
        "provenance": {"video_sha256": "A" * 64},
        "artifacts": {
            name: {
                "events_jsonl": "events.jsonl",
                "features_jsonl": "features.jsonl",
                "indicator_features_jsonl": "indicator-features.jsonl",
                "scores_jsonl": "scores.jsonl",
                "event_feature_errors_json": "event-feature-errors.json",
            }[name]
            for name in ARTIFACT_NAMES
        },
        "artifact_sha256": {
            name: f"{index + 1:X}" * 64
            for index, name in enumerate(ARTIFACT_NAMES)
        },
    }


def _ledger(summary: dict | None = None, *, summary_sha256: str = "F" * 64) -> dict:
    payload = summary or _summary()
    entry = build_scoring_run_bundle_entry(
        summary=payload,
        scoring_summary_sha256=summary_sha256,
        entry_id="video-001:run-v1",
        review_id="run-review-v1",
        reviewer_id="release-reviewer",
        review_source_sha256="B" * 64,
        reviewed_at="2026-08-29T10:00:00Z",
        registered_at="2026-08-29T11:00:00Z",
    )
    return build_trusted_scoring_run_bundle_ledger(
        entries=[entry],
        ledger_id="trusted-run-bundles",
        ledger_version="trusted-run-bundles-v1",
        authority_id="release-owner",
        registered_at="2026-08-29T12:00:00Z",
    )


class ScoringRunBundleBindingTests(unittest.TestCase):
    def test_exact_bundle_is_content_addressed_and_traceable(self) -> None:
        summary = _summary()
        ledger = _ledger(summary)

        authorization = authorize_scoring_run_bundle(
            summary=summary,
            scoring_summary_sha256="F" * 64,
            ledger=ledger,
        )

        identity = scoring_run_bundle_identity(
            summary, scoring_summary_sha256="F" * 64
        )
        self.assertTrue(authorization["trusted_scoring_run_bundle_verified"])
        self.assertEqual(authorization["entry_id"], "video-001:run-v1")
        self.assertEqual(
            authorization["bundle_root_sha256"],
            scoring_run_bundle_root_sha256(identity),
        )
        self.assertEqual(authorization["video_sha256"], "a" * 64)
        self.assertEqual(
            ledger["entries"][0]["bundle_identity"]["artifact_sha256"][
                "events_jsonl"
            ],
            "1" * 64,
        )

    def test_coordinated_summary_and_artifact_rewrite_is_not_authorized(self) -> None:
        original = _summary()
        ledger = _ledger(original)
        rewritten = copy.deepcopy(original)
        rewritten["artifact_sha256"]["events_jsonl"] = "E" * 64

        with self.assertRaisesRegex(
            ScoringRunBundleBindingError, "no unique active exact entry"
        ):
            authorize_scoring_run_bundle(
                summary=rewritten,
                scoring_summary_sha256="D" * 64,
                ledger=ledger,
            )

    def test_wrong_video_revocation_and_duplicate_active_root_fail_closed(self) -> None:
        summary = _summary()
        ledger = _ledger(summary)

        wrong_video = copy.deepcopy(summary)
        wrong_video["video_id"] = "video-002"
        with self.assertRaisesRegex(
            ScoringRunBundleBindingError, "no unique active exact entry"
        ):
            authorize_scoring_run_bundle(
                summary=wrong_video,
                scoring_summary_sha256="F" * 64,
                ledger=ledger,
            )

        revoked = copy.deepcopy(ledger)
        revoked["entries"][0]["status"] = "revoked"
        with self.assertRaisesRegex(
            ScoringRunBundleBindingError, "no unique active exact entry"
        ):
            authorize_scoring_run_bundle(
                summary=summary,
                scoring_summary_sha256="F" * 64,
                ledger=revoked,
            )

        duplicate = copy.deepcopy(ledger["entries"][0])
        duplicate["entry_id"] = "duplicate-entry"
        ledger["entries"].append(duplicate)
        with self.assertRaisesRegex(
            ScoringRunBundleBindingError, "duplicate active bundle roots"
        ):
            validate_trusted_scoring_run_bundle_ledger(ledger)

        distinct_summary = copy.deepcopy(summary)
        distinct_summary["artifact_sha256"]["features_jsonl"] = "E" * 64
        second_entry = build_scoring_run_bundle_entry(
            summary=distinct_summary,
            scoring_summary_sha256="D" * 64,
            entry_id="video-001:run-v2",
            review_id="run-review-v2",
            reviewer_id="release-reviewer",
            review_source_sha256="C" * 64,
            reviewed_at="2026-08-29T10:30:00Z",
            registered_at="2026-08-29T11:30:00Z",
        )
        distinct_ledger = _ledger(summary)
        distinct_ledger["entries"].append(second_entry)
        with self.assertRaisesRegex(
            ScoringRunBundleBindingError, "multiple active entries for video_id"
        ):
            validate_trusted_scoring_run_bundle_ledger(distinct_ledger)

    def test_invalid_review_order_unknown_fields_and_template_are_rejected(self) -> None:
        ledger = _ledger()
        ledger["entries"][0]["authorization_review"]["reviewed_at"] = (
            "2026-08-29T11:30:00Z"
        )
        with self.assertRaisesRegex(
            ScoringRunBundleBindingError, "must not postdate registration"
        ):
            validate_trusted_scoring_run_bundle_ledger(ledger)

        ledger = _ledger()
        ledger["entries"][0]["untrusted_override"] = True
        with self.assertRaisesRegex(
            ScoringRunBundleBindingError, "unsupported fields"
        ):
            validate_trusted_scoring_run_bundle_ledger(ledger)

        with self.assertRaisesRegex(
            ScoringRunBundleBindingError, "artifact_scope is invalid"
        ):
            load_trusted_scoring_run_bundle_ledger(
                ROOT / "calibration" / "trusted-scoring-run-bundles.template.json"
            )

    def test_contract_schema_accepts_the_runtime_validated_ledger(self) -> None:
        schema = json.loads(
            (ROOT / "contracts" / "trusted-scoring-run-bundles.schema.json").read_text(
                encoding="utf-8"
            )
        )
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(
            schema, format_checker=FormatChecker()
        ).validate(_ledger())

    def test_all_real_m78_summaries_can_be_bound_without_promoting_scores(self) -> None:
        run_root = ROOT / "reports" / "scoring-candidate-multivideo-m78" / "runs"
        for summary_path in sorted(run_root.glob("*/scoring-loop-summary.json")):
            with self.subTest(video=summary_path.parent.name):
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
                summary_sha256 = sha256_file(summary_path)
                entry = build_scoring_run_bundle_entry(
                    summary=summary,
                    scoring_summary_sha256=summary_sha256,
                    entry_id=f"test-only:{summary['video_id']}",
                    review_id="test-only-real-shape-review",
                    reviewer_id="unit-test",
                    review_source_sha256="C" * 64,
                    reviewed_at="2026-08-29T10:00:00Z",
                    registered_at="2026-08-29T11:00:00Z",
                )
                ledger = build_trusted_scoring_run_bundle_ledger(
                    entries=[entry],
                    ledger_id="test-only-real-m78-shape",
                    ledger_version="test-only-v1",
                    authority_id="unit-test",
                    registered_at="2026-08-29T12:00:00Z",
                )
                authorization = authorize_scoring_run_bundle(
                    summary=summary,
                    scoring_summary_sha256=summary_sha256,
                    ledger=ledger,
                )
                self.assertEqual(authorization["video_id"], summary["video_id"])
                self.assertEqual(summary["grade_counts"], {})
                self.assertEqual(
                    summary["safety_assertions"][
                        "any_non_null_grade_without_calibration"
                    ],
                    False,
                )


if __name__ == "__main__":
    unittest.main()
