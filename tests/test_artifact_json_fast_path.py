"""Bounded C JSON admission matches the former streaming byte contract."""
from __future__ import annotations

import json
import math
import random
import unittest
from unittest.mock import patch

from rallymate_scoring import artifact_evidence as evidence


def stream_bytes(value, max_bytes=evidence.DEFAULT_MAX_RECORD_BYTES, allow_nan=True):
    output = bytearray()
    encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=allow_nan)
    for chunk in encoder.iterencode(value):
        data = chunk.encode("utf-8")
        if len(output) + len(data) > max_bytes:
            raise evidence.ArtifactEvidenceError(
                f"评分证据单条记录超过内存预算（{max_bytes} bytes）；处理已停止，原始产物保留")
        output.extend(data)
    return bytes(output)


class ArtifactJsonFastPathTests(unittest.TestCase):
    def assert_same(self, value, **options):
        try:
            expected = stream_bytes(value, **options)
        except Exception as error:
            with self.assertRaises(type(error)) as actual:
                evidence._json_bytes(value, **options)
            self.assertEqual(str(actual.exception), str(error))
        else:
            actual = evidence._json_bytes(value, **options)
            self.assertEqual(actual, expected)
            admitted = evidence._bounded_json_snapshot(value, options.get("max_bytes", evidence.DEFAULT_MAX_RECORD_BYTES),
                                                        options.get("allow_nan", True), None)
            if admitted is not None:
                self.assertLessEqual(len(actual), admitted[1])
                self.assertLessEqual(admitted[1], min(options.get("max_bytes", evidence.DEFAULT_MAX_RECORD_BYTES), 1024 * 1024))

    def test_unicode_escaping_order_numbers_and_nan_are_byte_exact(self):
        for value in [None, True, False, 0, -1, 2**4095, -(2**4095), 2**20000, -0.0, 5e-324,
                      -1.7976931348623157e308, math.inf, -math.inf, math.nan,
                      '中文🙂\\"\n\r\t' + ''.join(chr(i) for i in range(32)),
                      {"z": [1, 2.0, -0.0], "a": {"🙂": "é", "中文": (None, True, False)}}]:
            with self.subTest(kind=type(value).__name__):
                self.assert_same(value)

    def test_random_nested_builtin_values_match_stream_and_proven_bound(self):
        rng = random.Random(16384)
        def value(depth):
            if depth < 4 and rng.randrange(4) == 0:
                return {str(index): value(depth + 1) for index in range(rng.randrange(6))}
            if depth < 4 and rng.randrange(4) == 0:
                return [value(depth + 1) for _ in range(rng.randrange(8))]
            return rng.choice([None, True, False, rng.randrange(-(2**100), 2**100),
                               rng.uniform(-1e200, 1e200), "控制\n字符\\🙂", ""])
        for _ in range(150):
            self.assert_same(value(0))

    def test_exact_encoded_budget_and_one_byte_under_preserve_old_behavior(self):
        value = {"proof": ["中文🙂", -0.0, 10**100, True]}
        size = len(stream_bytes(value))
        self.assert_same(value, max_bytes=size)
        self.assert_same(value, max_bytes=size - 1)
        self.assert_same(value, max_bytes=0)
        self.assert_same(value, max_bytes=-1)

    def test_nonfinite_rejection_surrogates_unknown_types_and_sort_errors_fall_back(self):
        for value, options in [([math.nan], {"allow_nan": False}), ([math.inf], {"allow_nan": False}),
                               ({"prefix": "long", "bad": "\ud800"}, {}),
                               ({1: "numeric", "x": "string"}, {}), (complex(1, 2), {}), ({1, 2}, {})]:
            with self.subTest(value_type=type(value).__name__):
                with patch.object(evidence.json, "dumps", side_effect=AssertionError("must use stream")):
                    self.assert_same(value, **options)

    def test_non_string_keys_and_subclasses_keep_streaming_contract(self):
        class Text(str):
            pass
        class Number(int):
            pass
        class CustomList(list):
            pass
        for value in [{1: "one", 2: "two"}, {None: "null"}, Text("text"), Number(7), CustomList([1, 2])]:
            with patch.object(evidence.json, "dumps", side_effect=AssertionError("must use stream")):
                self.assert_same(value)

    def test_depth_nodes_bytes_and_cycles_do_not_reach_c_encoder(self):
        deep = 1
        for _ in range(evidence._JSON_FAST_MAX_DEPTH + 1):
            deep = [deep]
        cyclic = []
        cyclic.append(cyclic)
        cases = [deep, [0] * (evidence._JSON_FAST_MAX_NODES + 1),
                 "a" * (evidence._JSON_FAST_MAX_BYTES // 6 + 1), cyclic]
        for value in cases:
            with patch.object(evidence.json, "dumps", side_effect=AssertionError("must use stream")):
                self.assert_same(value)

    def test_shared_child_is_counted_each_time_and_private_snapshot_cannot_grow(self):
        child = ["x", 1]
        source = {"a": child, "b": child}
        snapshot, bound = evidence._bounded_json_snapshot(source, 1000, True, None)
        self.assertIsNot(snapshot, source)
        self.assertIsNot(snapshot["a"], child)
        self.assertIsNot(snapshot["a"], snapshot["b"])
        child.extend(["enlarged"] * 1000)
        self.assertLessEqual(len(stream_bytes(snapshot)), bound)
        self.assertEqual(snapshot, {"a": ["x", 1], "b": ["x", 1]})

    def test_callback_before_c_encode_cannot_enlarge_proven_snapshot(self):
        source = {"a": [1]}
        count = 0
        def callback():
            nonlocal count
            count += 1
            if count == 2:
                source["a"].append("large" * 1000)
        self.assertEqual(evidence._json_bytes(source, max_bytes=100, cancellation_callback=callback), b'{"a":[1]}')

    def test_admission_counts_punctuation_if_callback_grows_a_container(self):
        source = [False] * 300
        def callback():
            source.extend([False] * 10)
        snapshot, bound = evidence._bounded_json_snapshot(source, 2000, True, callback)
        self.assertEqual(len(snapshot), 310)
        self.assertEqual(len(stream_bytes(snapshot)), bound)

    def test_cancel_before_during_and_after_fast_encode_propagates(self):
        class Cancelled(RuntimeError):
            pass
        for value, target in [({"a": 1}, 1), ([0] * 1000, 2), ({"a": 1}, 3)]:
            calls = 0
            def callback():
                nonlocal calls
                calls += 1
                if calls == target:
                    raise Cancelled("cancelled")
            with self.assertRaisesRegex(Cancelled, "cancelled"):
                evidence._json_bytes(value, cancellation_callback=callback)
            self.assertEqual(calls, target)


if __name__ == "__main__":
    unittest.main()
