from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import re
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "frontend_scoring_reference_builder", ROOT / "scripts/build_frontend_scoring_reference.py"
)
assert SPEC is not None and SPEC.loader is not None
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def walk_values(value):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from walk_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_values(child)


class FrontendScoringReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source_bytes = builder.DEFAULT_SOURCE.read_bytes()
        cls.audit = json.loads(cls.source_bytes)
        cls.catalog = builder.build_catalog(cls.audit)
        cls.rules = {item["id"]: item for item in cls.catalog["rules"]}

    def test_all_298_source_cards_are_present_with_only_13_partial_measurement_links(self):
        source_ids = {item["indicator_id"] for item in self.audit["indicators"]}
        self.assertEqual(len(self.catalog["rules"]), 298)
        self.assertEqual(set(self.rules), source_ids)
        self.assertEqual(sum(key.startswith("GS") for key in self.rules), 248)
        self.assertEqual(sum(key.startswith("FS") for key in self.rules), 50)
        linked = {key for key, rule in self.rules.items()
                  if rule["implementation"]["kind"] == "related_2d_measurement"}
        expected = {item["indicator_id"] for item in self.audit["indicators"]
                    if item["runtime_summary"]["current_feasibility_level"] == "F2"}
        self.assertEqual(linked, expected)
        self.assertEqual(len(linked), 13)
        for item in self.audit["indicators"]:
            implementation = self.rules[item["indicator_id"]]["implementation"]
            if item["indicator_id"] in linked:
                self.assertEqual(implementation["features"], item["implementation"]["current_feasibility"]["required_features"])
                self.assertIn("尚不能完整判断", implementation["note"])
            else:
                self.assertEqual(implementation["kind"], "not_implemented")
                self.assertEqual(implementation["features"], [])

    def test_all_displayed_original_fields_and_five_grades_are_verbatim(self):
        source_fields = {"id": "指标编号", "name": "指标名称", "definition": "技术定义",
                         "calculation": "计算方式", "required_points": "所需点", "unevaluable": "不可评价"}
        event_names = {item["event_code"]: item["name_zh"] for item in self.audit["formal_events"]["GS"]}
        event_names.update({item["event_id"]: item["name"] for item in self.audit["formal_events"]["FS"]})
        for original in self.audit["indicators"]:
            rule = self.rules[original["indicator_id"]]
            fields = original["source_fields"]
            with self.subTest(indicator=rule["id"]):
                for key, source_key in source_fields.items():
                    self.assertEqual(rule[key], fields[source_key]["text"])
                self.assertEqual(rule["grades"], [{"grade": grade, "definition": fields[f"{grade}级"]["text"]}
                                                 for grade in "ABCDE"])
                self.assertEqual(rule["event_code"], original["event_code"])
                self.assertEqual(rule["event_name"], event_names[original["event_code"]])
                self.assertEqual(rule["stage"], fields.get("所属阶段", {}).get("text", ""))
        # Deliberately preserve this source error instead of silently repairing it.
        self.assertEqual(self.rules["GS01-M10-04"]["definition"], "GS-02 双手反手")

    def test_source_positions_and_document_hashes_trace_back_to_the_audit(self):
        documents = {item["id"]: item for item in self.catalog["source_documents"]}
        self.assertEqual(len(documents), 7)
        for original in self.audit["source_documents"]:
            self.assertEqual(documents[original["source_id"]], {
                "id": original["source_id"], "name": original["filename"], "sha256": original["sha256"]})
        for original in self.audit["indicators"]:
            fields = original["source_fields"]
            source_positions = set()
            for field in fields.values():
                if "locator" in field:
                    source_positions.add((field["locator"], field.get("table_cell")))
                else:
                    source_positions.update(zip(field["locators"], field["cells"]))
            if original["domain"] == "FS":
                event = next(item for item in self.audit["formal_events"]["FS"] if item["event_id"] == original["event_code"])
                source_positions.add((event["event_heading"]["locator"], event["event_heading"].get("table_cell")))
            sources = self.rules[original["indicator_id"]]["sources"]
            positions = {(source["location"], source.get("cell")) for source in sources}
            self.assertEqual(len(positions), len(sources))
            self.assertTrue(positions <= source_positions)
            for source in sources:
                self.assertIn(source["document_id"], documents)
                self.assertTrue(source["location"].startswith(source["document_id"] + ":"))
            for field_name in [*builder.TEXT_FIELDS.values(), *[f"{grade}级" for grade in "ABCDE"]]:
                field = fields[field_name]
                expected = {(field["locator"], field.get("table_cell"))} if "locator" in field else set(zip(field["locators"], field["cells"]))
                self.assertTrue(expected <= positions)

    def test_three_source_errors_and_97_missing_boundaries_are_flagged_without_rewriting(self):
        boundary_ids = {key for key, rule in self.rules.items() if builder.BOUNDARY_ISSUE in rule.get("source_issues", [])}
        expected_boundaries = {item["indicator_id"] for item in self.audit["indicators"] if item["domain"] == "GS"
                               and item["source_fields"]["开始标志性动作"]["text"] == ""
                               and item["source_fields"]["结束标志性动作"]["text"] == ""}
        self.assertEqual(boundary_ids, expected_boundaries)
        self.assertEqual(len(boundary_ids), 97)
        conflict_ids = {key for key, rule in self.rules.items()
                        if any(issue != builder.BOUNDARY_ISSUE for issue in rule.get("source_issues", []))}
        self.assertEqual(conflict_ids, {"GS01-M10-04", "GS02-M01-01", "GS02-M01-02"})
        for conflict in self.audit["conflicts"]:
            if conflict["id"] in ("GS-CONFLICT-01", "GS-CONFLICT-02"):
                for indicator_id in conflict["indicator_ids"]:
                    self.assertTrue(any(conflict["detail"] in issue for issue in self.rules[indicator_id]["source_issues"]))

    def test_scale_direction_and_cross_event_differences_use_readable_notes(self):
        differences = {item["id"]: item for item in self.audit["implementation_differences"]}
        for difference_id in builder.SPECIAL_DIFFERENCES:
            difference = differences[difference_id]
            for indicator_id in difference["indicator_ids"]:
                self.assertIn(builder.IMPLEMENTATION_NOTES[difference_id], self.rules[indicator_id]["implementation"]["note"])
        for indicator_id, terms in {
            "FS01-M02": ["下降幅度", "膝角变化", "连续性", "典型值", "峰值"],
            "FS01-M04": ["髋宽", "人体尺度", "分母不同", "不能确认真实触地"],
            "FS01-M05": ["第一步启动", "间隔", "未关联"],
            "FS02-M02": ["来球方向", "目标方向", "不等于"],
            "FS09-M05": ["启动或回位", "没有关联", "不能证明衔接"],
        }.items():
            for term in terms:
                self.assertIn(term, self.rules[indicator_id]["implementation"]["note"])
        for rule in self.rules.values():
            self.assertIsNone(re.search(r"F2|median|peak|body.scale|target_direction|proxy", rule["implementation"]["note"]))
            if rule["implementation"]["kind"] == "related_2d_measurement":
                self.assertIn("不能直接证明", rule["implementation"]["note"])
                self.assertIn("尚不能完整判断", rule["implementation"]["note"])

    def test_24_visual_techniques_preserve_all_phases_and_clauses_with_optional_stages(self):
        techniques = self.catalog["visual_techniques"]
        self.assertEqual(len(techniques), 24)
        self.assertEqual([item["id"] for item in techniques], [item["technique_id"] for item in self.audit["technique_mappings"]])
        stages = {item["id"]: item for item in self.audit["visual_stage_event_mappings"]}
        clauses = {item["id"]: item for item in self.audit["visual_rules"]}
        visited_stages, visited_clauses, optional_stages = set(), set(), set()
        for technique, original in zip(techniques, self.audit["technique_mappings"]):
            self.assertEqual(technique["name"], original["name"])
            self.assertEqual([phase["id"] for phase in technique["phases"]], original["source_stage_ids"])
            for phase in technique["phases"]:
                source = stages[phase["id"]]
                self.assertEqual(phase["name"], source["source_heading"])
                self.assertEqual(phase["optional"], source["optional_in_source"])
                visited_stages.add(phase["id"])
                if phase["optional"]:
                    optional_stages.add(phase["id"])
                expected = []
                for role in ("definition", "recognition"):
                    expected.extend((p["text"], p["source_ref"]) for p in source["source_role_paragraphs"][role])
                for clause_id in source["rule_ids"]:
                    expected.append((clauses[clause_id]["source_text"], clauses[clause_id]["source_ref"]))
                    visited_clauses.add(clause_id)
                self.assertEqual(len(phase["clauses"]), len(expected))
                for actual, (text, reference) in zip(phase["clauses"], expected):
                    self.assertEqual(actual["text"], text)
                    self.assertEqual(actual["source"]["location"], reference["locator"])
                    self.assertEqual(actual["source"].get("cell"), reference.get("table_cell"))
        self.assertEqual(len(visited_stages), 68)
        self.assertEqual(len(visited_clauses), 246)
        self.assertEqual(optional_stages, {"VIS-NET-T003", "VIS-NET-T008", "VIS-NET-T014"})

    def test_public_catalog_has_no_private_paths_or_invented_scoring_configuration(self):
        forbidden_keys = {"path", "xpath", "weight", "weights", "score", "score_mapping", "grade_mapping",
                          "numeric_thresholds", "score_thresholds", "aggregation_formula", "deduction_rule"}
        for value in walk_values(self.catalog):
            if isinstance(value, dict):
                self.assertFalse(set(value) & forbidden_keys)
            elif isinstance(value, str):
                self.assertIsNone(re.search(r"(?i)[a-z]:[\\/]|[\\/](?:users|home)[\\/]|xwechat_files|file://", value))
        findings = " ".join(item["detail"] for item in self.catalog["findings"])
        self.assertIn("没有技术百分制换算", findings)
        self.assertIn("13 项", findings)
        self.assertIn("五个主阶段", findings)
        self.assertIn("十个细阶段", findings)

    def test_generation_is_deterministic_and_does_not_mutate_the_source(self):
        before = copy.deepcopy(self.audit)
        first = builder.render_catalog(builder.build_catalog(self.audit))
        second = builder.render_catalog(builder.build_catalog(self.audit))
        self.assertEqual(first, second)
        self.assertEqual(first, builder.DEFAULT_OUTPUT.read_bytes())
        self.assertEqual(self.audit, before)
        self.assertEqual(hashlib.sha256(builder.DEFAULT_SOURCE.read_bytes()).digest(), hashlib.sha256(self.source_bytes).digest())

    def test_check_detects_a_stale_artifact_without_overwriting_it(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            output = Path(directory) / "catalog.json"
            output.write_bytes(b"stale artifact")
            self.assertEqual(builder.main(["--output", str(output), "--check"]), 1)
            self.assertEqual(output.read_bytes(), b"stale artifact")
            self.assertEqual(builder.main(["--output", str(output)]), 0)
            self.assertEqual(builder.main(["--output", str(output), "--check"]), 0)
            self.assertEqual(output.read_bytes(), builder.DEFAULT_OUTPUT.read_bytes())

    def test_cli_refuses_to_overwrite_the_source_audit(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            builder.main(["--output", str(builder.DEFAULT_SOURCE)])
        self.assertEqual(raised.exception.code, 2)
        self.assertEqual(builder.DEFAULT_SOURCE.read_bytes(), self.source_bytes)

    def test_unexpected_source_versions_counts_and_unsafe_references_fail_closed(self):
        mutations = [
            lambda source: source.update(schema_version="2.0.0"),
            lambda source: source.update(production_scoring_configuration=True),
            lambda source: source["indicators"].pop(),
            lambda source: source["source_documents"][0].update(filename="C:\\private\\source.docx"),
            lambda source: source["source_documents"][0].update(sha256="not-a-hash"),
            lambda source: source["indicators"][0]["source_fields"]["技术定义"].update(locators=["C:\\private\\source.docx"]),
            lambda source: source["visual_rules"][0].update(stage_id="VIS-FS-T999"),
            lambda source: source["visual_stage_event_mappings"][0].update(optional_in_source=None),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(case=index):
                source = copy.deepcopy(self.audit)
                mutate(source)
                with self.assertRaises(ValueError):
                    builder.build_catalog(source)


if __name__ == "__main__":
    unittest.main()
