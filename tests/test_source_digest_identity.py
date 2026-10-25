"""SHA text casing must not discard otherwise bound model measurements."""
from __future__ import annotations

import copy
import unittest

from rallymate_features.source_alignment import JOINTS
from rallymate_scoring.indicator_assessment import build_indicator_assessment, source_model_geometry
from rallymate_service.source_assessment import _model_parent
from tests.test_source_model_measurements import fixture, project


def preload_fixture():
    artifact, events = fixture()
    record = artifact["records"][0]
    item = record["indicators"][0]
    item["indicator_id"] = "FS01-M02"
    metric = item["source_measurements"][0]
    metric.update(feature_name="preload_hip_reference_dy_px", value=-2.5, unit="px",
                  source_ref="CARD-FS:word/document.xml:P0115")
    metric["coverage"]["required_joint_names"] = list(JOINTS[:4])
    item["source_rule_gate"]["pose_coverage"]["required_joint_names"] = list(JOINTS)
    for payload in (events[0], record, metric):
        window = payload["source_window"]
        window["video_sha256"] = window["video_sha256"].upper()
        for binding in window["source_bindings"]:
            binding["video_sha256"] = binding["video_sha256"].upper()
        payload["geometry_mask"] = {joint: [True] * len(window["source_frames"]) for joint in JOINTS}
    torso = copy.deepcopy(metric)
    torso.update(feature_name="preload_hip_reference_dy_torso", value=0., unit="torso")
    item["source_measurements"].append(torso)
    return artifact, events


def metrics(report):
    card = next(row for row in report["indicators"] if row["indicator_id"] == "FS01-M02")
    return card["windows"][0]["measurements"]


class SourceDigestIdentityTests(unittest.TestCase):
    def test_uppercase_proof_keeps_values_and_evidence_through_both_consumers(self):
        artifact, events = preload_fixture()
        before = copy.deepcopy((artifact, events))
        report = project(artifact, events)
        self.assertEqual([m["value"] for m in metrics(report)], [-2.5, 0.])
        self.assertTrue(all(m["status"] == "measured" for m in metrics(report)))
        for actual, original in zip(metrics(report), artifact["records"][0]["indicators"][0]["source_measurements"]):
            for field in ("source_window", "geometry_mask", "measurement_provenance", "source_frames", "evidence"):
                self.assertEqual(actual[field], original[field])
            self.assertIsNone(actual["model_geometry_coverage"]["source_visibility_70_percent_verified"])
        result = build_indicator_assessment({"job_id": "model-video"}, source_assessment=report)
        rows = [r for r in result["observations"] if r.get("indicator_id") == "FS01-M02"]
        self.assertEqual({r["feature_name"]: r["value"] for r in rows},
                         {"preload_hip_reference_dy_px": -2.5, "preload_hip_reference_dy_torso": 0.})
        self.assertTrue(all(r["full_source_rule_satisfied"] is False for r in rows))
        self.assertEqual(result["counts"]["formal_graded"], 0)
        self.assertEqual((artifact, events), before)

    def test_expected_digest_requires_exact_ascii_hex_and_same_identity(self):
        artifact, events = preload_fixture()
        record = artifact["records"][0]
        metric = record["indicators"][0]["source_measurements"][0]
        for expected in ("a" * 64, "A" * 64, "aA" * 32):
            with self.subTest(expected=expected):
                self.assertIsNotNone(_model_parent(events[0], record, expected))
                self.assertTrue(source_model_geometry(metric, JOINTS[:4], person_track_id=217,
                                                     video_sha256=expected)["passes"])
        for expected in (None, True, 0, [], {}, "b" * 64, "a" * 63, "a" * 65,
                         " " + "a" * 64, "a" * 64 + "\n", "g" + "a" * 63, "Ａ" * 64):
            with self.subTest(expected=expected):
                self.assertIsNone(_model_parent(events[0], record, expected))
                self.assertIsNone(source_model_geometry(metric, JOINTS[:4], person_track_id=217,
                                                       video_sha256=expected))

    def test_valid_casing_does_not_relax_binding_geometry_or_visibility_gates(self):
        for defect in ("digest", "binding_case", "pts", "source_track", "joint_missing", "joint_false",
                       "provenance", "window", "source_frames", "visibility"):
            with self.subTest(defect=defect):
                artifact, events = preload_fixture()
                metric = artifact["records"][0]["indicators"][0]["source_measurements"][0]
                if defect == "digest":
                    metric["source_window"]["video_sha256"] = "B" * 64
                elif defect == "binding_case":
                    metric["source_window"]["source_bindings"][0]["video_sha256"] = "a" * 64
                elif defect == "pts":
                    metric["source_window"]["source_bindings"][0]["pts"] += 1
                elif defect == "source_track":
                    events[0]["track_diagnostics"]["source_track_ids"] = [1]
                elif defect == "joint_missing":
                    metric["geometry_mask"].pop("left_shoulder")
                elif defect == "joint_false":
                    metric["geometry_mask"]["left_shoulder"] = [False] * 3
                elif defect == "provenance":
                    metric.pop("measurement_provenance")
                elif defect == "window":
                    metric["window_ms"]["end_ms"] = 120
                elif defect == "source_frames":
                    metric["source_frames"] = [10, 11, 99]
                elif defect == "visibility":
                    metric["coverage"]["source_visibility_70_percent_verified"] = True
                report = project(artifact, events)
                self.assertEqual(metrics(report)[0]["status"], "unavailable")
                self.assertIsNone(metrics(report)[0]["value"])
                result = build_indicator_assessment({"job_id": "model-video"}, source_assessment=report)
                self.assertFalse(any(row.get("feature_name") == "preload_hip_reference_dy_px"
                                     for row in result["observations"]))

    def test_indicator_rejects_changed_digest_after_valid_projection(self):
        artifact, events = preload_fixture()
        report = project(artifact, events)
        self.assertEqual(metrics(report)[0]["value"], -2.5)
        report["video_sha256"] = "b" * 64
        result = build_indicator_assessment({"job_id": "model-video"}, source_assessment=report)
        self.assertFalse(any(row.get("indicator_id") == "FS01-M02" for row in result["observations"]))


if __name__ == "__main__":
    unittest.main()
