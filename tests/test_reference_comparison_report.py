"""Receipt-bound reference reports, explicit pair selection and safe HTML data."""
from contextlib import redirect_stdout
from copy import deepcopy
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import compare_reference_assessments as renderer
from tests.test_reference_comparison import fixture


class _Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self.data = []
        self.inside_data = False
        self.images = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script":
            self.scripts.append(attrs)
            self.inside_data = attrs.get("id") == "data"
        if tag == "img":
            self.images.append(attrs)

    def handle_endtag(self, tag):
        if tag == "script":
            self.inside_data = False

    def handle_data(self, data):
        if self.inside_data:
            self.data.append(data)


class ReferenceComparisonReportTests(unittest.TestCase):
    def test_checked_in_visual_review_matches_pinned_digest_after_checkout(self):
        directory = renderer.ROOT / "docs/evidence/djokovic-comparison-20261112"
        manifest = json.loads((directory / "pairs.json").read_text(encoding="utf-8"))
        source = directory / manifest["selection"]["visual_review_file"]
        # JSON files use LF in .gitattributes. Hash the checked-out artifact,
        # including its line endings, just as the real replay command does.
        self.assertNotIn(b"\r\n", source.read_bytes())
        self.assertEqual(renderer.sha(source), manifest["selection"]["visual_review_sha256"])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "assessments").mkdir()
        self.reports = {"candidate": fixture("b" * 64, episode_id="user-action"),
                        "reference": fixture("a" * 64, episode_id="reference-action")}
        for name, report in self.reports.items():
            report["source_run_id"] = name + "-run"
            report["dimensions"] = [{"id": "preparation", "metrics": [{"id": "coil"}]},
                                    {"id": "striking", "metrics": [{"id": "head_control"}]}]
            report["episodes"][0]["metrics"] = {
                "coil": {"value": 20, "score": 40 if name == "candidate" else 95},
                "head_control": {"value": .05, "score": 70 if name == "candidate" else 85},
            }
        self.receipt = {"all_passed": True, "source_inputs_stable": True,
                        "assessment_files_stable": True, "results": []}
        self.write_reports_and_receipt()
        self.manifest = {"selection": {"method": "explicit_source_actions", "prior_results_seen": True},
                         "source_attribution": {"author": "source owner"},
                         "excluded_candidates": [{"label": "rejected slice", "reason": "different technique"}],
                         "pairs": [{"id": "pair-1", "label": "Two independently recorded forehands",
                                    "candidate": {"sample_id": "candidate", "episode_id": "user-action"},
                                    "reference": {"sample_id": "reference", "episode_id": "reference-action"}}]}

    def write_json(self, path, value):
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")

    def write_receipt(self):
        self.write_json(self.root / "validation-results.json", self.receipt)

    def write_reports_and_receipt(self):
        self.receipt["results"] = []
        for name, report in self.reports.items():
            relative = f"assessments/{name}.json"
            path = self.root / relative
            self.write_json(path, report)
            self.receipt["results"].append({"sample_id": name, "run_id": report["source_run_id"],
                                           "video_sha256": report["source_video_sha256"],
                                           "assessment_recomputed": True, "assessment_path": relative,
                                           "assessment_sha256": renderer.sha(path)})
        self.write_receipt()

    def build(self):
        return renderer.build_comparisons(self.root, self.manifest)

    def attach_visual_review(self):
        review = {"sources": [], "reviews": []}
        self.manifest["excluded_candidates"] = []
        self.manifest["pairs"][0]["visual_review"] = {}
        for name, report in self.reports.items():
            episode = report["episodes"][0]
            review["sources"].append({"source_id": name, "video_sha256": report["source_video_sha256"]})
            review["reviews"].append({"review_id": name + "-review", "source_id": name,
                                      "episode_id": episode["episode_id"],
                                      "source_window_ms": {key: episode[key] for key in ("start_ms", "peak_ms", "end_ms")},
                                      "report_subject_binding": {key: episode[key] for key in (
                                          "subject_id", "person_track_id", "source_selection_epoch", "camera_reference_epoch")},
                                      "comparison_status": "three_visible_phases_accepted", "complete_stroke_core_visible": True,
                                      "visual_technique": episode["motion_pattern"]["profile_id"]})
            self.manifest["pairs"][0]["visual_review"][name + "_review_id"] = name + "-review"
        self.manifest["source_visual_review"] = review
        return review

    def test_real_comparison_preserves_distinct_action_scores_and_receipt_hashes(self):
        before = deepcopy((self.manifest, self.reports))
        result = self.build()
        self.assertEqual(result["comparison_count"], 1)
        self.assertTrue(result["all_pairs_scored"])
        row = result["comparisons"][0]
        self.assertEqual(row["candidate_action_score"]["overall"], 55)
        self.assertEqual(row["reference_action_score"]["overall"], 90)
        self.assertEqual(row["comparison"]["reference_similarity_score"], 100)
        self.assertTrue(row["comparison"]["evaluation_eligible"])
        self.assertEqual(result["source_receipt_sha256"], renderer.sha(self.root / "validation-results.json"))
        self.assertEqual(result["assessment_file_sha256"], {
            name: renderer.sha(self.root / f"assessments/{name}.json") for name in self.reports})
        self.assertEqual(result["excluded_candidates"], self.manifest["excluded_candidates"])
        self.assertEqual(before, (self.manifest, self.reports))

    def test_source_bound_visual_review_accepts_matching_actions(self):
        self.attach_visual_review()
        result = self.build()
        self.assertTrue(result["all_expected_statuses_matched"])
        self.assertEqual(result["comparisons"][0]["visual_review"]["candidate_review_id"], "candidate-review")

    def test_visual_review_cannot_move_between_video_action_window_or_subject(self):
        changes = (lambda r: r["sources"][0].update(video_sha256="f" * 64),
                   lambda r: r["reviews"][0].update(episode_id="reference-action"),
                   lambda r: r["reviews"][0]["source_window_ms"].update(peak_ms=123),
                   lambda r: r["reviews"][0]["report_subject_binding"].update(person_track_id=99))
        for change in changes:
            review = self.attach_visual_review()
            change(review)
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "visual_review_source_binding_mismatch"):
                self.build()

    def test_visually_excluded_action_cannot_enter_accepted_pair(self):
        review = self.attach_visual_review()
        review["reviews"][0].update(comparison_status="excluded_incomplete_stroke", complete_stroke_core_visible=False)
        with self.assertRaisesRegex(ValueError, "visual_review_comparison_status_mismatch"):
            self.build()

    def test_visual_review_technique_must_match_bound_report(self):
        review = self.attach_visual_review()
        review["reviews"][0]["visual_technique"] = "backhand_slice"
        with self.assertRaisesRegex(ValueError, "visual_review_technique_mismatch"):
            self.build()

    def test_passed_receipt_does_not_authorize_subsequently_tampered_report(self):
        self.reports["candidate"]["episodes"][0]["metrics"]["coil"]["score"] = 100
        self.write_json(self.root / "assessments/candidate.json", self.reports["candidate"])
        with self.assertRaisesRegex(ValueError, "assessment_not_bound_to_recomputation_receipt"):
            self.build()

    def test_report_byte_hash_is_required_even_when_json_content_is_equivalent(self):
        path = self.root / "assessments/candidate.json"
        path.write_text(json.dumps(self.reports["candidate"], separators=(",", ":")), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "assessment_not_bound"):
            self.build()

    def test_receipt_must_bind_source_run_video_path_and_actual_recomputation(self):
        for field, value in (("run_id", "foreign-run"), ("video_sha256", "f" * 64),
                             ("assessment_path", "assessments/reference.json"),
                             ("assessment_sha256", "f" * 64), ("assessment_recomputed", False)):
            with self.subTest(field=field):
                self.write_reports_and_receipt()
                self.receipt["results"][0][field] = value
                self.write_receipt()
                with self.assertRaisesRegex(ValueError, "assessment_not_bound"):
                    self.build()

    def test_duplicate_receipt_sample_is_rejected(self):
        self.receipt["results"].append(deepcopy(self.receipt["results"][0]))
        self.write_receipt()
        with self.assertRaisesRegex(ValueError, "duplicate_source_receipt"):
            self.build()

    def test_missing_receipt_sample_is_rejected(self):
        self.receipt["results"].pop(0)
        self.write_receipt()
        with self.assertRaisesRegex(ValueError, "assessment_not_bound"):
            self.build()

    def test_failed_and_nonboolean_passed_receipts_are_rejected(self):
        for passed in (False, None, 1, "true"):
            with self.subTest(passed=passed):
                self.receipt["all_passed"] = passed
                self.write_receipt()
                with self.assertRaisesRegex(ValueError, "source_recomputation_not_passed"):
                    self.build()

    def test_duplicate_pair_id_is_rejected(self):
        self.manifest["pairs"].append(deepcopy(self.manifest["pairs"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate_comparison_id"):
            self.build()

    def test_duplicate_action_selection_is_rejected_even_with_new_label(self):
        pair = deepcopy(self.manifest["pairs"][0])
        pair.update(id="pair-2", label="Same evidence with a different label")
        self.manifest["pairs"].append(pair)
        with self.assertRaisesRegex(ValueError, "duplicate_comparison_selection"):
            self.build()

    def test_missing_pairs_and_path_traversal_are_rejected(self):
        for pairs in (None, [], "pair"):
            with self.subTest(pairs=pairs):
                with self.assertRaisesRegex(ValueError, "comparison_pairs_missing"):
                    renderer.build_comparisons(self.root, {"pairs": pairs})
        self.manifest["pairs"][0]["candidate"]["sample_id"] = "../foreign"
        with self.assertRaisesRegex(ValueError, "invalid_sample_id"):
            self.build()

    def test_incompatible_pair_stays_visible_with_no_invented_score(self):
        self.reports["candidate"]["episodes"][0]["motion_pattern"]["profile_id"] = "baseline_two_hand_backhand"
        self.write_reports_and_receipt()
        result = self.build()
        self.assertEqual(result["comparison_count"], 1)
        self.assertFalse(result["all_pairs_scored"])
        row = result["comparisons"][0]
        self.assertEqual(row["candidate_action_score"]["overall"], 55)
        self.assertEqual(row["comparison"]["status"], "unavailable")
        self.assertIsNone(row["comparison"]["reference_similarity_score"])
        self.assertIn("technique_profile_mismatch", row["comparison"]["reason_codes"])

    def test_report_changed_during_comparison_is_rejected(self):
        real_compare = renderer.compare_action_to_reference
        def change(*args):
            result = real_compare(*args)
            path = self.root / "assessments/candidate.json"
            path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
            return result
        with patch.object(renderer, "compare_action_to_reference", side_effect=change), \
                self.assertRaisesRegex(ValueError, "assessment_changed_during_comparison"):
            self.build()

    def test_receipt_changed_during_comparison_is_rejected(self):
        real_compare = renderer.compare_action_to_reference
        def change(*args):
            result = real_compare(*args)
            self.receipt["all_passed"] = False
            self.write_receipt()
            return result
        with patch.object(renderer, "compare_action_to_reference", side_effect=change), \
                self.assertRaisesRegex(ValueError, "source_receipt_changed_during_comparison"):
            self.build()

    def test_html_cannot_close_data_script_or_inject_tags_from_manifest(self):
        attack = '</script><script>alert("injected")</script><img src=x onerror=alert(1)>'
        self.manifest["pairs"][0]["label"] = attack
        self.manifest["source_attribution"]["author"] = attack
        payload = self.build()
        html = renderer.render(payload)
        parsed = _Scripts()
        parsed.feed(html)
        self.assertEqual(len(parsed.scripts), 2)
        self.assertEqual(parsed.images, [])
        data = "".join(parsed.data)
        self.assertNotIn("<", data)
        self.assertIn("\\u003c/script>", data)
        self.assertEqual(json.loads(data), payload)
        self.assertNotIn(attack, html)

    def test_nonfinite_data_cannot_be_serialized_into_html(self):
        with self.assertRaises(ValueError):
            renderer.render({"comparison": float("nan")})

    def test_cli_writes_json_and_html_with_identical_embedded_payload(self):
        pairs = self.root / "pairs.json"
        self.write_json(pairs, self.manifest)
        output = self.root / "rendered"
        argv = ["compare_reference_assessments.py", "--input", str(self.root), "--pairs", str(pairs), "--output", str(output)]
        with patch("sys.argv", argv), redirect_stdout(io.StringIO()):
            renderer.main()
        payload = json.loads((output / "comparison.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["pairs_manifest_sha256"], renderer.sha(pairs))
        self.assertEqual(payload["comparison_code_sha256"], renderer.sha(renderer.ROOT / "src/rallymate_scoring/reference_comparison.py"))
        parsed = _Scripts()
        parsed.feed((output / "comparison.html").read_text(encoding="utf-8"))
        self.assertEqual(json.loads("".join(parsed.data)), payload)


if __name__ == "__main__":
    unittest.main()
