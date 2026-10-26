import unittest

from scripts.evaluate_tennis_assessment import distributions, validate_manifest


def sample(identifier, group="player-a", split="holdout", label="ordinary", **extra):
    return dict(sample_id=identifier, source_group=group, split=split, evaluation_label=label, **extra)


class EvaluationManifestTests(unittest.TestCase):
    def test_rejects_same_person_across_development_and_holdout(self):
        with self.assertRaisesRegex(ValueError, "leaks"):
            validate_manifest({"samples": [sample("a", split="development"), sample("b")]})

    def test_rejects_same_source_disguised_as_new_player(self):
        with self.assertRaisesRegex(ValueError, "identical source"):
            validate_manifest({"samples": [sample("a", source_video_sha256="a" * 64),
                                           sample("b", group="player-b", source_video_sha256="a" * 64)]})

    def test_hash_case_does_not_hide_duplicate_sources(self):
        with self.assertRaisesRegex(ValueError, "identical source"):
            validate_manifest({"samples": [sample("a", source_video_sha256="A" * 64),
                                           sample("b", group="player-b", source_video_sha256="a" * 64)]})

    def test_rejects_one_source_counted_in_both_identity_groups(self):
        with self.assertRaisesRegex(ValueError, "inconsistent labels"):
            validate_manifest({"samples": [sample("a"), sample("b", label="professional")]})

    def test_same_source_rerun_cannot_change_its_group_mean(self):
        # Group-count deduplication alone still lets repeated inference alter
        # the group mean from 50 to 70. Exact-source repeats must be separate.
        rows = [sample("original", score=50, source_video_sha256="a" * 64),
                sample("repeat", score=90, source_video_sha256="A" * 64)]
        with self.assertRaisesRegex(ValueError, "duplicate source"):
            validate_manifest({"samples": rows})

    def test_explicitly_excluded_or_robustness_same_source_records_are_retained(self):
        rows = [sample("original", score=50, source_video_sha256="a" * 64),
                sample("repeat-excluded", score=90, source_video_sha256="A" * 64, include_in_distribution=False),
                sample("repeat-robustness", split="robustness", score=99, source_video_sha256="a" * 64)]
        validate_manifest({"samples": rows})
        result = distributions(rows)["holdout"]
        self.assertEqual(result["groups"], 1)
        self.assertEqual(result["ordinary_mean"], 50.)

    def test_robustness_variants_and_repeats_do_not_inflate_groups(self):
        rows = [sample("a", score=50), sample("b", score=70),
                sample("compressed", split="robustness", score=10),
                sample("pro", group="player-pro", label="professional", score=80),
                sample("failed", group="player-failed", score=None)]
        validate_manifest({"samples": rows})
        result = distributions(rows)["holdout"]
        self.assertEqual(result["groups"], 2)
        self.assertEqual(result["attempted_groups"], 3)
        self.assertEqual(result["unscored_groups"], 1)
        self.assertEqual(result["ordinary_mean"], 60)
        self.assertEqual(result["pairs"], 1)
        self.assertEqual(result["professional_above_ordinary_pair_fraction"], 1)

    def test_invalid_primary_subject_is_retained_in_attempt_denominator(self):
        rows = [sample("ordinary", score=70),
                sample("wrong-person", group="pro-source", label="professional", score=95,
                       include_in_distribution=False, evaluation_validity="invalid_subject_tracking")]
        result = distributions(rows)["holdout"]
        self.assertEqual(result["attempted_groups"], 2)
        self.assertEqual(result["groups"], 1)
        self.assertEqual(result["professional_groups"], 0)
        self.assertIsNone(result["professional_above_ordinary_pair_fraction"])


if __name__ == "__main__":
    unittest.main()
