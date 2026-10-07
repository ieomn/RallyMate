from __future__ import annotations

import io
import unittest

from scripts.run_regression_suites import AccountingResult, parse_args, partition_tests, result_summary


class NamedTest:
    def __init__(self, name):
        self.name = name

    def id(self):
        return self.name


def manifest(*selectors):
    return {"schema_version": "1.0.0", "historical": [
        {"selector": selector, "sources": ["reports/immutable-fixture.json"], "reason": "Published fixture dependency."}
        for selector in selectors
    ]}


class RegressionSuiteAccountingTests(unittest.TestCase):
    def test_default_executes_all_and_inventory_is_explicit(self):
        self.assertEqual(parse_args([]).suite, "all")
        self.assertFalse(parse_args([]).inventory)
        self.assertTrue(parse_args(["--inventory"]).inventory)

    def test_partition_is_exhaustive_and_preserves_new_runtime_tests(self):
        tests = [NamedTest("test_old.Archive.test_one"), NamedTest("test_runtime.Current.test_one"),
                 NamedTest("test_old.Archive.test_two"), NamedTest("test_new.New.test_new")]
        groups = partition_tests(tests, manifest("test_old.Archive"))
        self.assertEqual(groups["historical"], [tests[0], tests[2]])
        self.assertEqual(groups["runtime"], [tests[1], tests[3]])
        self.assertEqual(sum(len(items) for items in groups.values()), len(tests))

    def test_method_selector_does_not_remove_synthetic_siblings(self):
        tests = [NamedTest("test_mixed.Checks.test_archive"), NamedTest("test_mixed.Checks.test_synthetic")]
        groups = partition_tests(tests, manifest("test_mixed.Checks.test_archive"))
        self.assertEqual(groups["historical"], tests[:1])
        self.assertEqual(groups["runtime"], tests[1:])

    def test_stale_duplicate_and_overlapping_selectors_fail_closed(self):
        tests = [NamedTest("test_old.Archive.test_one")]
        for supplied in [manifest("test_old.Missing"), manifest("test_old.Archive", "test_old.Archive"),
                         manifest("test_old.Archive", "test_old.Archive.test_one")]:
            with self.subTest(supplied=supplied), self.assertRaises(ValueError):
                partition_tests(tests, supplied)

    def test_fixture_error_never_turns_unstarted_methods_into_passes(self):
        class BrokenFixture(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                raise ValueError("original registry binding mismatch")

            def test_one(self):
                pass

            def test_two(self):
                pass

        result = unittest.TextTestRunner(stream=io.StringIO(), resultclass=AccountingResult).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(BrokenFixture))
        summary = result_summary(result, discovered=2)
        self.assertEqual(summary["discovered_test_methods"], 2)
        self.assertEqual(summary["started_test_methods"], 0)
        self.assertEqual(summary["passed_test_methods"], 0)
        self.assertEqual(summary["error_records"], 1)
        self.assertFalse(summary["successful"])

    def test_subtest_failures_keep_record_and_method_denominators_separate(self):
        class Subtests(unittest.TestCase):
            def test_subtests(self):
                for value in (1, 2):
                    with self.subTest(value=value):
                        self.assertEqual(value, 0)

        result = unittest.TextTestRunner(stream=io.StringIO(), resultclass=AccountingResult).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(Subtests))
        summary = result_summary(result, discovered=1)
        self.assertEqual(summary["started_test_methods"], 1)
        self.assertEqual(summary["passed_test_methods"], 0)
        self.assertEqual(summary["failure_records"], 2)
        self.assertFalse(summary["successful"])


if __name__ == "__main__":
    unittest.main()
