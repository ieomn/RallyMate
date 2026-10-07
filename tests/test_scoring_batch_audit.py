from __future__ import annotations

import copy
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from scripts.audit_scoring_batch import (
    Checks, FEATURES, WEIGHTS, audit_entry, audit_manifest, audit_source_contract, audit_training,
    expected_indicator, main, normalized_records, sha256,
)
from rallymate_service.training_evaluation import build_training_evaluation


def record(indicator="FS01-M03", event="event-1", confidence=.7):
    return {"video_id": "job-1", "person_track_id": 1, "event_id": event,
            "indicator_id": indicator, "event_code": indicator[:4], "feature_status": "measured",
            "features": [{"feature_name": name, "value": 1., "valid": True,
                          "confidence": confidence, "source_frames": [0, 1]} for name in FEATURES[indicator]],
            "quality_gate": {"measurement_allowed": True, "scoring_allowed": True},
            "scoring_status": "calibration_required", "grade": None}


class ScoringBatchFormulaAuditTests(unittest.TestCase):
    def check(self, records):
        checks = Checks()
        value = build_training_evaluation(records)
        answer = audit_training(value, records, checks)
        failures = [item for item in checks.items if item["status"] == "failed"]
        self.assertEqual(failures, [])
        return answer

    def test_independent_formula_matches_single_missing_and_repeated_inputs(self):
        self.check([])
        single = self.check([record(confidence=None)])
        self.assertEqual(single["available_indicator_count"], 1)
        self.assertEqual(single["score_0_to_100"], 100)
        self.check([record(), record(event="event-2")])

    def test_python_half_even_and_two_level_mean(self):
        low = [record(confidence=.5), record(event="event-2", confidence=.5)]
        high = [record("FS01-M04", confidence=.7), record("FS01-M04", event="event-2", confidence=.7)]
        a = expected_indicator("FS01-M03", low)
        b = expected_indicator("FS01-M04", high)
        self.assertEqual((a["raw_score"], a["score_0_to_100"]), (92.5, 92))
        self.assertEqual((b["raw_score"], b["score_0_to_100"]), (95.5, 96))
        answer = self.check(low + high)
        self.assertEqual(answer["rounded_indicator_mean"], 94)
        self.assertEqual(answer["score_0_to_100"], 94)

    def test_conflicting_duplicate_and_wrong_event_cannot_add_evidence(self):
        first = record()
        duplicate = copy.deepcopy(first)
        conflicting = copy.deepcopy(first)
        conflicting["features"][0]["value"] = 10
        bad_event = record("FS01-M04")
        bad_event["event_code"] = "FS02"
        groups, validation = normalized_records([first, duplicate, conflicting, bad_event])
        self.assertEqual(groups["FS01-M03"], [])
        self.assertEqual(validation["conflicting_record_count"], 3)
        self.assertEqual(validation["event_code_mismatch_record_count"], 1)
        self.check([first, duplicate, conflicting, bad_event])

    def test_repeatability_uses_circular_angles_and_category_frequency(self):
        rows = [record("FS02-M03", event=f"event-{index}") for index in range(3)]
        for row, angle, code in zip(rows, (359., 1., 0.), (1., 2., 1.)):
            for feature in row["features"]:
                if feature["feature_name"] == "launch_direction_deg":
                    feature["value"] = angle
                elif feature["feature_name"] == "drive_side_code":
                    feature["value"] = code
        self.check(rows)
        # Mixed unavailable events affect coverage; they must not add a false
        # independent zero-valued observation to the repeatability sample.
        unavailable = record("FS02-M03", event="event-4")
        unavailable["quality_gate"]["measurement_allowed"] = False
        self.check(rows + [unavailable])

    def test_changed_weight_score_or_explanation_is_reported(self):
        rows = [record()]
        original = build_training_evaluation(rows)
        for field, changed in (("score_0_to_100", 11), ("effective_component_weights", {"repeatability": 1.}),
                               ("components", {"median_feature_confidence_percent": 100})):
            value = copy.deepcopy(original)
            value["indicator_evaluations"][1][field] = changed
            checks = Checks()
            audit_training(value, rows, checks)
            self.assertTrue(any(c["status"] == "failed" and c["code"] == "FS01-M03." + field for c in checks.items))
        value = copy.deepcopy(original)
        value["score_explanation"]["rounding_adjustment_points"] = 5
        checks = Checks()
        audit_training(value, rows, checks)
        self.assertTrue(any(c["status"] == "failed" and c["code"] == "overall_explanation.total_rounding" for c in checks.items))

    def test_invalid_confidence_is_excluded_and_failure_is_json_safe(self):
        for invalid in (True, float("nan"), float("inf"), 10**1000):
            rows = [record(confidence=invalid)]
            self.assertIsNone(expected_indicator("FS01-M03", rows)["components"]["median_feature_confidence_percent"])
        checks = Checks()
        checks.expect("bad", {("tuple", "key"): float("nan")}, {})
        encoded = json.dumps(checks.items, allow_nan=False)
        self.assertIn("nonfinite_or_oversized_number", encoded)


class ScoringBatchSnapshotAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.mp4"
        self.source.write_bytes(b"Synthetic file-hash fixture, not a video or inferred evidence")
        self.frames = self.root / "frames.jsonl"
        self.frames.write_text("\n".join(json.dumps({"job_id": "job-1", "frame": {
            "index": i, "processed_index": i, "timestamp_ms": i * 40, "timestamp_source": "decoder_pts"}}) for i in range(2)), encoding="utf-8")
        self.rows = [record()]
        self.loop = {"video_id": "job-1", "provenance": {"pipeline_job_id": "job-1",
            "video_sha256": sha256(self.source), "frames_sha256": sha256(self.frames), "calibration_assets": []},
            "result_state": {"grade": None}, "grade_counts": {}}
        self.summary = {"job_id": "job-1", "status": "completed", "input": {
            "video_path": str(self.source), "video": {"frame_count": 2}},
            "processing": {"processed_frames": 2, "source_start_frame": 0, "source_end_frame_exclusive": 2,
                "frame_stride": 1, "last_processed_timestamp_ms": 40, "source_timing": {"timing_verified": True}},
            "minimum_scoring_loop": self.loop}
        self.entry = {"job_id": "job-1", "state": "verified", "source_path": str(self.source),
            "source_sha256": sha256(self.source), "metadata": {"frame_count": 2},
            "local_pipeline_artifacts": {"frames.jsonl": str(self.frames)}, "artifacts": {}}
        response = self.root / "job-response.json"
        response.write_text(json.dumps({"id": "job-1", "status": "succeeded"}))
        self.entry["job_response_path"] = str(response)
        scores = [{**{k: row[k] for k in ("video_id", "person_track_id", "event_id", "indicator_id", "event_code")},
                   "grade": None, "status": "calibration_required"} for row in self.rows]
        events = [{"video_id": "job-1", "person_track_id": 1, "event_id": "event-1",
                   "event_code": "FS01", "start_ms": 0, "end_ms": 40}]
        for name, value in {"summary": self.summary, "scoring-loop-summary.json": self.loop,
                "demo_result": {"job_id": "job-1", "training_evaluation": build_training_evaluation(self.rows),
                    "formal_scoring": {"available": False, "grade": None, "score_0_to_100": None}},
                "indicator-features.jsonl": self.rows, "scores.jsonl": scores, "events.jsonl": events}.items():
            self.write_artifact(name, value)

    def write_artifact(self, name, value):
        path = self.root / name
        text = "\n".join(json.dumps(row) for row in value) if name.endswith("jsonl") else json.dumps(value)
        path.write_text(text, encoding="utf-8")
        self.entry["artifacts"][name] = {"path": str(path), "sha256": sha256(path), "size_bytes": path.stat().st_size}

    def audit(self):
        return audit_entry(self.entry, self.root)

    def test_complete_fixture_passes_and_source_files_stay_unchanged(self):
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        answer = self.audit()
        self.assertEqual(answer["status"], "passed", [x for x in answer["checks"] if x["status"] == "failed"])
        self.assertEqual(answer["formal"]["emitted_grade_count"], 0)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})

    def test_tampered_source_hash_and_frame_gap_fail(self):
        self.source.write_bytes(b"Changed source")
        self.assertEqual(self.audit()["status"], "failed")
        self.frames.write_text(json.dumps({"job_id": "job-1", "frame": {"index": 1, "processed_index": 1,
            "timestamp_ms": 40, "timestamp_source": "decoder_pts"}}))
        answer = self.audit()
        failed = {c["code"] for c in answer["checks"] if c["status"] == "failed"}
        self.assertIn("video.frame_rows", failed)
        self.assertIn("video.frame_errors", failed)

    def test_unapproved_grade_fails_even_with_self_reported_authority(self):
        score = {**{k: self.rows[0][k] for k in ("video_id", "person_track_id", "event_id", "indicator_id", "event_code")},
                 "grade": "A", "status": "scored", "feasibility_level": "F4", "threshold_version": "claimed",
                 "evidence": {"source_frames": [0]}, "quality_gate": {"scoring_allowed": True}}
        self.write_artifact("scores.jsonl", [score])
        self.loop["provenance"].update(calibration_assets=["claimed"], runtime_authorization_files={"claimed": True})
        self.loop["grade_counts"] = {"A": 1}
        self.write_artifact("scoring-loop-summary.json", self.loop)
        self.write_artifact("summary", self.summary)
        answer = self.audit()
        self.assertEqual(answer["status"], "failed")
        self.assertIn("formal.trusted_authorization", [c["code"] for c in answer["checks"] if c["status"] == "failed"])

    def test_pending_and_failed_entries_never_become_passes(self):
        manifest = self.root / "manifest.json"
        pending = {"job_id": "next", "state": "running"}
        manifest.write_text(json.dumps({"entries": [self.entry, pending]}))
        report = audit_manifest(manifest, self.root)
        self.assertEqual(report["status"], "pending")
        self.assertEqual(report["counts"], {"passed": 1, "pending": 1})
        self.assertEqual(report["entries"][1]["checks"], [])
        failed = audit_entry({"job_id": "bad", "state": "failed", "error": "Known pipeline failure"}, self.root)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["reason"], "Known pipeline failure")

    def test_completed_missing_artifact_fails_and_cli_preserves_existing_output(self):
        del self.entry["artifacts"]["events.jsonl"]
        self.assertEqual(self.audit()["status"], "failed")
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps({"entries": [self.entry]}))
        output = self.root / "audit.json"
        output.write_text("preserved")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(["--manifest", str(manifest), "--output", str(output)])
        self.assertEqual(output.read_text(), "preserved")

    def test_source_contract_requires_actual_matching_word_bytes(self):
        folder = self.root / "originals"
        folder.mkdir()
        documents = []
        for index in range(7):
            path = folder / f"source-{index}.docx"
            path.write_bytes(f"Source hash fixture {index}".encode())
            documents.append({"source_id": f"DOC-{index}", "path": path.name, "sha256": sha256(path)})
        indicators = [{"indicator_id": key, "implementation": {"scoring_requirements": {
            "required_feature_names": list(value)}}, "runtime_summary": {"formal_scoring_enabled": False}}
            for key, value in FEATURES.items()]
        indicators += [{"indicator_id": f"unimplemented-{i}"} for i in range(298 - len(indicators))]
        contract = {"artifact_scope": "source_reference_audit", "validation": {"status": "passed"},
            "explicit_rules": {"numeric_score_cutpoints_provided_by_sources": False,
                "numeric_weights_provided_by_sources": False, "deduction_formula_provided_by_sources": False},
            "source_documents": documents, "indicators": indicators}
        path = self.root / "source-contract.json"
        path.write_text(json.dumps(contract))
        self.assertEqual(audit_source_contract(path, self.root)["status"], "failed")
        self.assertEqual(audit_source_contract(path, self.root, [folder])["status"], "passed_source_binding_only")
        (folder / "source-0.docx").write_bytes(b"tampered")
        result = audit_source_contract(path, self.root, [folder])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["technical_validity"], "not_established")

    def test_foreign_event_and_indicator_identity_are_not_accepted(self):
        self.write_artifact("events.jsonl", [{"video_id": "other-video", "person_track_id": 1,
            "event_id": "event-1", "event_code": "FS01", "start_ms": 0, "end_ms": 40}])
        result = self.audit()
        self.assertEqual(result["status"], "failed")
        self.assertTrue(any(c["code"] == "formal.record_anomalies" and c["status"] == "failed" for c in result["checks"]))


if __name__ == "__main__":
    unittest.main()
