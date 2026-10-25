from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rallymate_service import foot_crossing_candidates as service
from rallymate_scoring.source_assessment import write_foot_crossing_candidates
from tests.test_foot_crossing_candidates import crossing_fixture


JOB = "11111111-1111-4111-8111-111111111111"
SOURCE = "22222222-2222-4222-8222-222222222222"


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def producer_code():
    root = Path(__file__).resolve().parents[1]
    return [{"name": name, "bytes": len(raw), "sha256": digest(raw)}
            for name in sorted(service.CORE_CODE) for raw in [(root / name).read_bytes()]]


def fixture(root, *, model=False, zero=False):
    output = root / "runs" / JOB
    output.mkdir(parents=True)
    sequence, timeline = crossing_fixture(model=model)
    if zero:
        sequence.keypoints_xy["left_ankle"][1, 0] = .48
    events = []
    for name, raw in (("frames.jsonl", b"synthetic saved observations\n"),
                      ("events.jsonl", b""), ("primary-player.jsonl", encoded(timeline))):
        (output / name).write_bytes(raw)
    write_foot_crossing_candidates(output, sequence, events, video_id=SOURCE,
                                  video_sha256="a" * 64, primary_timeline=timeline)
    inputs = {key: {"name": name, "bytes": (output / name).stat().st_size,
                    "sha256": digest((output / name).read_bytes()), "stat": service.file_stat(output / name)}
              for key, name in service.INPUT_NAMES.items()}
    summary = {"job_id": JOB, "source_failed_job_id": SOURCE,
               "minimum_scoring_loop": {"video_id": SOURCE, "provenance": {"pipeline_job_id": SOURCE,
                    "video_sha256": "a" * 64, "frames_sha256": inputs["frames"]["sha256"]},
                    "artifact_sha256": {"events_jsonl": inputs["events"]["sha256"]}},
               "recovery": {"source_failed_job_id": SOURCE,
                    "source_artifact_sha256": {item["name"]: item["sha256"] for item in inputs.values()}}}
    (output / "summary.json").write_bytes(encoded(summary))
    job = {"id": JOB, "status": "succeeded", "summary": summary, "output_dir": str(output)}
    artifact = json.loads((output / service.ARTIFACT_NAME).read_bytes())
    manifest = {"schema_version": service.MANIFEST_VERSION, "display_job_id": JOB, "source_job_id": SOURCE,
                "video_id": SOURCE, "video_sha256": "a" * 64, "job_summary_sha256": service.canonical_sha(summary),
                "source_summary_sha256": digest((output / "summary.json").read_bytes()), "inputs": inputs,
                "input_bundle_sha256": service.input_bundle_sha(SOURCE, "a" * 64, inputs),
                "producer": {"algorithm_version": service.ALGORITHM_VERSION,
                    "code": producer_code()},
                "source_binding": service.source_binding(), "created_at_utc": "2026-10-19T00:00:00Z",
                "input_binding_mode": "verified_publication_snapshot",
                "artifact": {"name": service.ARTIFACT_NAME, "artifact_version": service.ARTIFACT_VERSION,
                    "algorithm_version": service.ALGORITHM_VERSION}}
    publish_fixture(root, manifest, artifact)
    return job, manifest, artifact


def publish_fixture(root, manifest, artifact, *, artifact_raw=None):
    raw = encoded(artifact) if artifact_raw is None else artifact_raw
    if isinstance(manifest.get("artifact"), dict):
        manifest["artifact"].update(bytes=len(raw), sha256=digest(raw))
    manifest_raw = encoded(manifest)
    manifest_sha = digest(manifest_raw)
    base = root / "derived-foot-crossing" / JOB
    directory = base / manifest_sha
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_bytes(manifest_raw)
    (directory / service.ARTIFACT_NAME).write_bytes(raw)
    (base / "index.json").write_bytes(encoded({"schema_version": service.INDEX_VERSION,
                                              "job_id": JOB, "manifest_sha256": manifest_sha}))
    return directory


class FootCrossingCandidatesServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.job, self.manifest, self.artifact = fixture(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def read(self):
        return service.load_foot_crossing_candidates(self.root, self.job)

    def assert_unavailable(self, result, status=None):
        self.assertNotEqual(result["status"], "available")
        if status:
            self.assertEqual(result["status"], status)
        self.assertIsNone(result["identity"])
        self.assertIsNone(result["counts"])
        self.assertIsNone(result["output_limits"])
        self.assertEqual(result["candidates"], [])
        self.assertFalse(result["scoring_allowed"])

    def test_real_synthetic_writer_projection_preserves_timing_and_distinct_track_ids(self):
        result = self.read()
        self.assertEqual(result["status"], "available", result)
        candidate = result["candidates"][0]
        self.assertEqual(candidate["candidate_quality_status"], "unvalidated")
        self.assertEqual(candidate["source_interval_ms"][0], {"numerator": 17000, "denominator": 3, "approx_ms": 17000 / 3})
        self.assertEqual(candidate["source_bindings"][0]["primary_player_id"], 1)
        self.assertEqual(candidate["source_bindings"][0]["person_track_id"], 8)
        self.assertEqual(candidate["processed_indexes"], [170, 171])
        self.assertNotIn("observations", candidate)
        self.assertNotIn("parent_events", candidate)
        self.assertNotIn("exclusion_examples", result)
        self.assertGreater(self.manifest["inputs"]["frames"]["stat"]["mtime_ns"], 2**53)

    def test_sam_keeps_unknown_confidence_and_not_assessed_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job, _, _ = fixture(root, model=True)
            result = service.load_foot_crossing_candidates(root, job)
            self.assertEqual(result["status"], "available", result)
            row = result["candidates"][0]
            self.assertIsNone(row["quality"]["rtm_confidence_min"])
            self.assertEqual(row["local_joint_diagnostics"]["assessment_status"], "sam_confidence_diagnostics_not_assessed")
            self.assertFalse(row["local_joint_diagnostics"]["empty_lists_prove_no_anomaly"])

    def test_negative_native_source_time_is_not_clamped_or_rounded(self):
        row = self.artifact["candidates"][0]
        for binding, value, rational in zip(row["source_bindings"], [-20, 0], row["source_interval_ms"]):
            binding.update(pts=value, source_timestamp_ms=value * 1000 / 600)
            rational.update(numerator=-100 if value else 0, denominator=3 if value else 1, approx_ms=value * 1000 / 600)
        row["axis"]["source_pts"] = [-20, 0]
        publish_fixture(self.root, self.manifest, self.artifact)
        result = self.read()
        self.assertEqual(result["status"], "available", result)
        self.assertEqual(result["candidates"][0]["source_bindings"][0]["pts"], -20)
        self.assertEqual(result["candidates"][0]["source_interval_ms"][0]["numerator"], -100)

    def test_zero_candidates_differs_from_missing_registration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job, _, _ = fixture(root, zero=True)
            result = service.load_foot_crossing_candidates(root, job)
            self.assertEqual(result["status"], "available", result)
            self.assertEqual(result["reason_code"], "available_zero_candidates")
            self.assertEqual(result["counts"]["candidates"], 0)
        result = service.load_foot_crossing_candidates(self.root, {"id": "legacy-job", "status": "succeeded", "summary": {}})
        self.assert_unavailable(result, "not_computed")

    def test_get_never_opens_large_input_contents(self):
        original = os.open
        def guarded(path, *args, **kwargs):
            self.assertNotIn(Path(path).name, service.INPUT_NAMES.values())
            return original(path, *args, **kwargs)
        with patch.object(service.os, "open", side_effect=guarded):
            self.assertEqual(self.read()["status"], "available")

    def test_corrupt_nested_objects_fail_closed(self):
        for value in (None, [], "wrong"):
            with self.subTest(value=value):
                changed = copy.deepcopy(self.manifest)
                changed["artifact"] = value
                publish_fixture(self.root, changed, self.artifact)
                self.assert_unavailable(self.read(), "invalid_artifact")
        publish_fixture(self.root, self.manifest, self.artifact)
        for key in ("minimum_scoring_loop", "recovery"):
            job = copy.deepcopy(self.job)
            job["summary"][key] = None
            self.assert_unavailable(service.load_foot_crossing_candidates(self.root, job), "binding_conflict")

    def test_rehashed_invalid_json_duplicate_nan_depth_and_size(self):
        examples = [(b'{"x":0,"x":1}', "invalid_artifact"), (b'{"x":NaN}', "invalid_artifact"),
                    (b'{"x":' + b'[' * 25 + b'0' + b']' * 25 + b'}', "limit_exceeded"),
                    (b' ' * (service.LIMITS["artifact_bytes"] + 1), "limit_exceeded")]
        for raw, status in examples:
            with self.subTest(status=status, size=len(raw)):
                publish_fixture(self.root, self.manifest, self.artifact, artifact_raw=raw)
                self.assert_unavailable(self.read(), status)

    def test_expected_hash_and_unsupported_versions(self):
        self.assertEqual(service.load_foot_crossing_candidates(self.root, self.job, self.manifest["artifact"]["sha256"].upper())["status"], "available")
        self.assert_unavailable(service.load_foot_crossing_candidates(self.root, self.job, "0" * 64), "binding_conflict")
        self.artifact["version"] = "projected-ankle-order-candidates-v9.0.0"
        publish_fixture(self.root, self.manifest, self.artifact)
        self.assert_unavailable(self.read(), "unsupported_version")

    def test_relabelled_v1_without_v2_parent_proof_is_rejected(self):
        for key in ("parent_source_association_counts", "parent_source_association_policy"):
            changed = copy.deepcopy(self.artifact)
            del changed["candidates"][0][key]
            publish_fixture(self.root, self.manifest, changed)
            self.assert_unavailable(self.read(), "invalid_artifact")
        self.artifact["candidates"][0]["parent_source_association_counts"] = {"weak_source_track_association": 2}
        publish_fixture(self.root, self.manifest, self.artifact)
        self.assert_unavailable(self.read(), "invalid_artifact")

    def test_approved_algorithm_requires_exact_reviewed_core_code_pins(self):
        self.manifest["producer"]["code"][0]["sha256"] = "e" * 64
        # The alphabetically first item is coordinates; its reviewed operator
        # snapshot is retained, while the two algorithm pins are fixed here.
        core = next(item for item in self.manifest["producer"]["code"] if item["name"] in service.SUPPORTED_CORE_SHA256)
        core["sha256"] = "e" * 64
        publish_fixture(self.root, self.manifest, self.artifact)
        self.assert_unavailable(self.read(), "unsupported_version")

    def test_current_and_historical_writer_identities_remain_supported(self):
        name = "src/rallymate_scoring/source_assessment.py"
        current = next(item for item in self.manifest["producer"]["code"] if item["name"] == name)
        self.assertEqual(self.read()["status"], "available")
        self.assertNotEqual(current["sha256"], service.SUPPORTED_CORE_SHA256[name])
        current.update(sha256=service.SUPPORTED_CORE_SHA256[name], bytes=service.SUPPORTED_CORE_BYTES[name])
        publish_fixture(self.root, self.manifest, self.artifact)
        self.assertEqual(self.read()["status"], "available")

    def test_writer_digest_and_size_cannot_be_mixed_across_supported_versions(self):
        name = "src/rallymate_scoring/source_assessment.py"
        current = next(item for item in self.manifest["producer"]["code"] if item["name"] == name)
        current_digest, current_size = current["sha256"], current["bytes"]
        for digest, size in ((current_digest, service.SUPPORTED_CORE_BYTES[name]),
                             (service.SUPPORTED_CORE_SHA256[name], current_size),
                             ("0" * 64, current_size)):
            with self.subTest(digest=digest, size=size):
                current.update(sha256=digest, bytes=size)
                publish_fixture(self.root, self.manifest, self.artifact)
                result = self.read()
                self.assert_unavailable(result, "unsupported_version")
                self.assertEqual(result["reason_code"], "unsupported_algorithm_code")

    def test_input_catalog_summary_and_stat_conflicts(self):
        for field in ("video_sha256", "frames_sha256", "events_sha256", "primary_timeline_sha256"):
            changed = copy.deepcopy(self.artifact)
            changed[field] = "d" * 64
            publish_fixture(self.root, self.manifest, changed)
            self.assert_unavailable(self.read(), "binding_conflict")
        changed = copy.deepcopy(self.artifact)
        changed["source_binding"]["reference_sha256"] = "d" * 64
        publish_fixture(self.root, self.manifest, changed)
        self.assert_unavailable(self.read(), "binding_conflict")
        publish_fixture(self.root, self.manifest, self.artifact)
        output = Path(self.job["output_dir"])
        (output / "frames.jsonl").write_bytes(b"changed")
        self.assert_unavailable(self.read(), "binding_conflict")

    def test_bool_gap_time_confidence_and_formal_claims_rejected(self):
        mutations = [lambda r: r["source_bindings"][0].update(frame_index=True),
                     lambda r: r["source_frames"].__setitem__(1, 175),
                     lambda r: r["source_interval_ms"][0].update(numerator=17001),
                     lambda r: r["quality"]["confidence"]["left_ankle"].__setitem__(0, .2),
                     lambda r: r.update(scoring_allowed=True),
                     lambda r: r["local_joint_diagnostics"]["keypoint_jump_candidate_frames_by_joint"].update(left_ankle=[999])]
        for mutation in mutations:
            changed = copy.deepcopy(self.artifact)
            mutation(changed["candidates"][0])
            publish_fixture(self.root, self.manifest, changed)
            self.assert_unavailable(self.read())

    def test_reason_category_counts_and_totals_must_match(self):
        cases = [
            ({"strict_projected_order_exchange": 2}, "inconsistent_counts"),
            ({"same_projected_order": 1}, "inconsistent_reason_counts"),
            ({"unknown_but_well_formed_reason": 1}, "inconsistent_reason_counts"),
        ]
        for reasons, reason_code in cases:
            with self.subTest(reasons=reasons):
                changed = copy.deepcopy(self.artifact)
                changed["counts"]["reasons"] = reasons
                publish_fixture(self.root, self.manifest, changed)
                result = self.read()
                self.assert_unavailable(result, "invalid_artifact")
                self.assertEqual(result["reason_code"], reason_code)
        changed = copy.deepcopy(self.artifact)
        changed["counts"].update(frames=3, adjacent_sequence_pairs=2, no_exchange=1,
                                 reasons={"strict_projected_order_exchange": 1, "unknown_but_well_formed_reason": 1})
        publish_fixture(self.root, self.manifest, changed)
        result = self.read()
        self.assert_unavailable(result, "invalid_artifact")
        self.assertEqual(result["reason_code"], "inconsistent_reason_counts")

    def test_unknown_well_formed_exclusion_reason_remains_compatible(self):
        self.artifact["counts"].update(frames=3, adjacent_sequence_pairs=2, excluded=1,
                                       reasons={"strict_projected_order_exchange": 1, "future_exclusion_reason": 1})
        self.artifact["output_limits"].update(exclusion_limit=0, exclusions_truncated=True)
        publish_fixture(self.root, self.manifest, self.artifact)
        result = self.read()
        self.assertEqual(result["status"], "available", result)
        self.assertEqual(result["counts"]["reasons"]["future_exclusion_reason"], 1)

    def test_quality_counts_cannot_undercount_retained_candidates(self):
        for quality in ("unvalidated", "suspect"):
            with self.subTest(retained_quality=quality):
                changed = copy.deepcopy(self.artifact)
                row = changed["candidates"][0]
                row["candidate_quality_status"] = quality
                if quality == "suspect":
                    row["local_joint_diagnostics"]["keypoint_jump_candidate_frames_by_joint"] = {"right_ankle": [171]}
                changed["counts"]["candidate_quality"] = {"suspect" if quality == "unvalidated" else "unvalidated": 1}
                publish_fixture(self.root, self.manifest, changed)
                result = self.read()
                self.assert_unavailable(result, "invalid_artifact")
                self.assertEqual(result["reason_code"], "inconsistent_quality_counts")

    def test_truncated_quality_counts_and_zero_retention_are_valid(self):
        self.artifact["counts"].update(frames=4, adjacent_sequence_pairs=3, candidates=2, no_exchange=1,
                                       candidate_quality={"unvalidated": 1, "suspect": 1},
                                       reasons={"strict_projected_order_exchange": 2, "same_projected_order": 1})
        self.artifact["output_limits"].update(candidate_limit=1, candidates_truncated=True)
        for retained in (1, 0):
            with self.subTest(retained=retained):
                self.artifact["output_limits"]["candidate_limit"] = retained
                self.artifact["candidates"] = self.artifact["candidates"][:retained]
                publish_fixture(self.root, self.manifest, self.artifact)
                result = self.read()
                self.assertEqual(result["status"], "available", result)
                self.assertEqual(result["output_limits"]["stored_candidate_count"], retained)
                self.assertEqual(result["counts"]["candidate_quality"], {"unvalidated": 1, "suspect": 1})

    def test_axis_length_and_direction_must_match_declared_displacement(self):
        mutations = [
            lambda axis: axis.update(hip_displacement_length_px=axis["hip_displacement_length_px"] * 2),
            lambda axis: axis.update(unit_vector=[-value for value in axis["unit_vector"]]),
            lambda axis: axis.update(hip_displacement_px=[0, 0]),
        ]
        for index, mutation in enumerate(mutations):
            with self.subTest(mutation=index):
                changed = copy.deepcopy(self.artifact)
                mutation(changed["candidates"][0]["axis"])
                publish_fixture(self.root, self.manifest, changed)
                result = self.read()
                self.assert_unavailable(result, "invalid_artifact")
                self.assertEqual(result["reason_code"], "inconsistent_axis_geometry")

    def test_axis_frontend_numeric_tolerance_is_preserved(self):
        axis = self.artifact["candidates"][0]["axis"]
        axis["hip_displacement_length_px"] *= 1 + .25e-6
        publish_fixture(self.root, self.manifest, self.artifact)
        result = self.read()
        self.assertEqual(result["status"], "available", result)
        self.assertEqual(result["candidates"][0]["axis"], {key: axis[key] for key in result["candidates"][0]["axis"]})

    def test_local_jump_endpoints_must_be_nonempty_unique_source_frames(self):
        for endpoints in ([], [171, 171], [True], [172], [170, 171, 170]):
            with self.subTest(endpoints=endpoints):
                changed = copy.deepcopy(self.artifact)
                changed["candidates"][0]["candidate_quality_status"] = "suspect"
                changed["counts"]["candidate_quality"] = {"suspect": 1}
                changed["candidates"][0]["local_joint_diagnostics"]["keypoint_jump_candidate_frames_by_joint"] = {"right_ankle": endpoints}
                publish_fixture(self.root, self.manifest, changed)
                result = self.read()
                self.assert_unavailable(result, "invalid_artifact")
                self.assertEqual(result["reason_code"], "invalid_jump_scope")

    def test_local_swap_endpoints_must_be_nonempty_unique_source_frames(self):
        for endpoints in ([], [171, 171], [True], [172], [170, 171, 170]):
            with self.subTest(endpoints=endpoints):
                changed = copy.deepcopy(self.artifact)
                changed["candidates"][0]["candidate_quality_status"] = "suspect"
                changed["counts"]["candidate_quality"] = {"suspect": 1}
                changed["candidates"][0]["local_joint_diagnostics"]["left_right_swap_candidate_joint_pairs"] = [
                    {"left_joint": "left_ankle", "right_joint": "right_ankle", "frames": endpoints}]
                publish_fixture(self.root, self.manifest, changed)
                result = self.read()
                self.assert_unavailable(result, "invalid_artifact")
                self.assertEqual(result["reason_code"], "invalid_swap_scope")

    def test_unique_local_endpoints_preserve_order_without_new_pair_restrictions(self):
        row = self.artifact["candidates"][0]
        row["candidate_quality_status"] = "suspect"
        self.artifact["counts"]["candidate_quality"] = {"suspect": 1}
        diagnostics = row["local_joint_diagnostics"]
        diagnostics["keypoint_jump_candidate_frames_by_joint"] = {"right_ankle": [171, 170], "left_hip": [170]}
        diagnostics["left_right_swap_candidate_joint_pairs"] = [{"left_joint": "left_hip", "right_joint": "right_ankle", "frames": [171, 170]}]
        publish_fixture(self.root, self.manifest, self.artifact)
        result = self.read()
        self.assertEqual(result["status"], "available", result)
        projected = result["candidates"][0]["local_joint_diagnostics"]
        self.assertEqual(projected["keypoint_jump_candidate_frames_by_joint"], diagnostics["keypoint_jump_candidate_frames_by_joint"])
        self.assertEqual(projected["left_right_swap_candidate_joint_pairs"], diagnostics["left_right_swap_candidate_joint_pairs"])

    def test_unknown_fields_cannot_leak_into_response(self):
        for value in (self.manifest["producer"], self.manifest["producer"]["code"][0], self.artifact["counts"],
                      self.artifact["output_limits"], self.artifact["candidates"][0]["source_interval_ms"][0]):
            value["private_extra"] = "do-not-return"
        publish_fixture(self.root, self.manifest, self.artifact)
        result = self.read()
        self.assertEqual(result["status"], "available", result)
        self.assertNotIn("do-not-return", json.dumps(result))

    def test_same_size_artifact_change_is_detected_even_without_stat_hint(self):
        directory = publish_fixture(self.root, self.manifest, self.artifact)
        path = directory / service.ARTIFACT_NAME
        before = path.stat()
        raw = path.read_bytes().replace(b"unvalidated", b"invalidated", 1)
        self.assertEqual(len(raw), before.st_size)
        path.write_bytes(raw)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assert_unavailable(self.read(), "binding_conflict")

    def test_publication_race_and_response_size_fail_closed(self):
        original = service._read
        reads = 0
        def changed_index(path, limit):
            nonlocal reads
            raw = original(path, limit)
            if path.name == "index.json":
                reads += 1
                if reads > 1:
                    return raw + b" "
            return raw
        with patch.object(service, "_read", side_effect=changed_index):
            self.assert_unavailable(self.read(), "binding_conflict")
        with patch.dict(service.LIMITS, response_bytes=100):
            self.assert_unavailable(self.read(), "limit_exceeded")

    def test_source_summary_uses_separate_bounded_node_budget(self):
        self.job["summary"]["synthetic_many_nodes"] = [0] * 125000
        raw = encoded(self.job["summary"])
        (Path(self.job["output_dir"]) / "summary.json").write_bytes(raw)
        self.manifest.update(job_summary_sha256=service.canonical_sha(self.job["summary"]), source_summary_sha256=digest(raw))
        publish_fixture(self.root, self.manifest, self.artifact)
        self.assertEqual(self.read()["status"], "available")
        self.job["summary"]["changed_db_only"] = True
        self.assert_unavailable(self.read(), "binding_conflict")

    def test_registered_candidates_accept_summary_larger_than_16_mib(self):
        self.job["summary"]["large_saved_report"] = "x" * (16 * 1024 * 1024)
        raw = encoded(self.job["summary"])
        self.assertGreater(len(raw), 16 * 1024 * 1024)
        self.assertLess(len(raw), service.LIMITS["summary_bytes"])
        (Path(self.job["output_dir"]) / "summary.json").write_bytes(raw)
        self.manifest.update(job_summary_sha256=service.canonical_sha(self.job["summary"]),
                             source_summary_sha256=digest(raw))
        publish_fixture(self.root, self.manifest, self.artifact)
        result = self.read()
        self.assertEqual(result["status"], "available", result)
        self.assertLess(len(encoded(result)), 1048576)
        self.assertNotIn("large_saved_report", result)

    def test_source_summary_byte_and_node_limits_still_fail_closed(self):
        path = Path(self.job["output_dir"]) / "summary.json"
        with path.open("wb") as stream:
            stream.truncate(service.LIMITS["summary_bytes"] + 1)
        result = self.read()
        self.assert_unavailable(result, "limit_exceeded")
        self.assertEqual(result["reason_code"], "file_size_limit")
        with self.assertRaises(service.CandidateArtifactError) as raised:
            service._json(b'{"many_nodes":[' + b'0,' * 999999 + b'0]}', node_limit=1000000)
        self.assertEqual(raised.exception.reason_code, "json_complexity_limit")

    def test_traversal_and_symlink_are_rejected(self):
        self.assert_unavailable(service.load_foot_crossing_candidates(self.root, {"id": "../escape", "status": "succeeded"}), "binding_conflict")
        index = self.root / "derived-foot-crossing" / JOB / "index.json"
        original = index.read_bytes()
        replacement = self.root / "index-copy.json"
        replacement.write_bytes(original)
        index.unlink()
        try:
            index.symlink_to(replacement)
        except OSError:
            self.skipTest("symlink creation requires platform privileges")
        self.assert_unavailable(self.read(), "binding_conflict")

    def test_control_exception_is_not_swallowed(self):
        with patch.object(service, "_read", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.read()


if __name__ == "__main__":
    unittest.main()
