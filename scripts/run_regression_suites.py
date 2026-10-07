"""Run the complete regression suite with explicit historical-fixture accounting.

Default: python scripts/run_regression_suites.py --suite all
Inventory only: --inventory
Focused run: --suite runtime or --suite historical (never a full-suite claim)

The manifest classifies dependencies, not acceptable failures. No assertions,
source hashes, reports, expectedFailure flags or skip markers are modified.
Historical failures remain failures and make the default all run exit nonzero.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import unittest
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "regression_suites.json"


def flatten(suite: unittest.TestSuite) -> Iterable[unittest.TestCase]:
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


def partition_tests(tests: list[unittest.TestCase], manifest: dict[str, Any]) -> dict[str, list[unittest.TestCase]]:
    if manifest.get("schema_version") != "1.0.0" or not isinstance(manifest.get("historical"), list):
        raise ValueError("unsupported regression suite manifest")
    entries = manifest["historical"]
    selectors = []
    for entry in entries:
        if (not isinstance(entry, dict) or not isinstance(entry.get("selector"), str)
                or not entry["selector"].startswith("test_") or not entry.get("reason")
                or not isinstance(entry.get("sources"), list) or not entry["sources"]):
            raise ValueError("historical entries require a named selector, source dependencies and reason")
        selectors.append(entry["selector"])
    if len(set(selectors)) != len(selectors):
        raise ValueError("duplicate historical selectors")
    result: dict[str, list[unittest.TestCase]] = {"runtime": [], "historical": []}
    matched = set()
    for test in tests:
        identifier = test.id()
        matches = [selector for selector in selectors if identifier == selector or identifier.startswith(selector + ".")]
        if len(matches) > 1:
            raise ValueError(f"overlapping historical selectors: {identifier}")
        matched.update(matches)
        result["historical" if matches else "runtime"].append(test)
    unmatched = sorted(set(selectors) - matched)
    if unmatched:
        raise ValueError("stale historical selectors: " + ", ".join(unmatched))
    return result


class AccountingResult(unittest.TextTestResult):
    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.success_count = 0

    def addSuccess(self, test: unittest.TestCase) -> None:
        super().addSuccess(test)
        self.success_count += 1


def result_summary(result: AccountingResult, *, discovered: int) -> dict[str, Any]:
    return {
        "discovered_test_methods": discovered,
        "started_test_methods": result.testsRun,
        "passed_test_methods": result.success_count,
        "failure_records": len(result.failures),
        "error_records": len(result.errors),
        "skipped_records": len(result.skipped),
        "expected_failure_records": len(result.expectedFailures),
        "unexpected_success_records": len(result.unexpectedSuccesses),
        "successful": result.wasSuccessful(),
        "failure_ids": [test.id() for test, _ in result.failures],
        "error_ids": [test.id() for test, _ in result.errors],
        "count_note": "Fixture errors and subtests are records, not independent test methods. Passed methods are counted from addSuccess, never obtained by subtraction.",
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("all", "runtime", "historical"), default="all")
    parser.add_argument("--inventory", action="store_true", help="Discover and report counts without running tests.")
    parser.add_argument("--json-summary", type=Path, help="Write a new summary file; existing files are never overwritten.")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    for path in (ROOT, ROOT / "src", ROOT / "tests"):
        sys.path.insert(0, str(path))
    # Tests invoke Python subprocesses, so propagate the same source roots.
    inherited = os.environ.get("PYTHONPATH")
    os.environ["PYTHONPATH"] = os.pathsep.join([str(ROOT / "src"), str(ROOT / "tests"), *([inherited] if inherited else [])])
    if args.json_summary:
        target = args.json_summary.resolve()
        protected = (ROOT / "reports", ROOT / "data")
        if any(target == path or path in target.parents for path in protected) or target.exists():
            raise ValueError("summary must be a new file outside immutable reports/data")
    manifest_bytes = MANIFEST.read_bytes()
    manifest = json.loads(manifest_bytes)
    tests = list(flatten(unittest.defaultTestLoader.discover(str(ROOT / "tests"))))
    partitioned = partition_tests(tests, manifest)
    chosen = ("runtime", "historical") if args.suite == "all" else (args.suite,)
    counts = {name: len(items) for name, items in partitioned.items()}
    summary: dict[str, Any] = {
        "schema_version": "1.0.0", "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "requested_suite": args.suite, "mode": "inventory" if args.inventory else "execution",
        "discovered_test_methods": len(tests), "partition_counts": counts,
        "selected_test_methods": sum(counts[name] for name in chosen),
        "not_selected_test_methods": sum(counts[name] for name in counts if name not in chosen),
        "is_full_suite_execution": args.suite == "all" and not args.inventory,
        "results": {},
        "scope_note": "runtime excludes only explicitly inventoried historical dependencies; a focused pass is not a full-suite pass. New or unclassified tests stay in runtime.",
    }
    print(json.dumps({key: value for key, value in summary.items() if key != "results"}, ensure_ascii=False), flush=True)
    if not args.inventory:
        for name in chosen:
            print(f"Running {name}: {counts[name]} discovered test methods", flush=True)
            result = unittest.TextTestRunner(verbosity=2 if args.verbose else 1, resultclass=AccountingResult).run(
                unittest.TestSuite(partitioned[name]))
            summary["results"][name] = result_summary(result, discovered=counts[name])
    summary["successful"] = None if args.inventory else all(item["successful"] for item in summary["results"].values())
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    if args.json_summary:
        args.json_summary.parent.mkdir(parents=True, exist_ok=True)
        with args.json_summary.open("x", encoding="utf-8") as output:
            json.dump(summary, output, ensure_ascii=False, indent=2)
            output.write("\n")
    return 0 if args.inventory or summary["successful"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
