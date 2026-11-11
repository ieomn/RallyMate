import math
import unittest

from rallymate_scoring.level_grading import DOMAINS, GATES, grade_level


class LevelGradingTests(unittest.TestCase):
    def test_all_five_major_weight_rows_match_the_supplied_guide(self):
        expected = ((70,30,0,0,0,0,0,0), (55,25,8,5,4,3,0,0),
                    (35,20,12,10,6,8,4,5), (25,17,14,12,8,10,5,9),
                    (18,14,15,12,8,10,8,15))
        for level, weights in enumerate(expected, 1):
            result = grade_level({}, target_level=level)
            self.assertEqual(tuple(result['domains'][d]['module_weight'] for d in DOMAINS), tuple(w/100 for w in weights))
            self.assertAlmostEqual(sum(result['domains'][d]['module_weight'] for d in DOMAINS), 1)

    def test_mutually_exclusive_backhand_is_normalized_not_double_counted(self):
        for style, retained, ignored in [('two_handed', 'GS02', 'GS03'), ('one_handed', 'GS03', 'GS02')]:
            result = grade_level({'GS-01': 100, retained: 30, ignored: 0}, target_level=2, backhand_style=style)
            self.assertEqual(result['overall_score'], 70)
            self.assertEqual(result['domains']['GS']['active_weights'], {'GS01': 4/7, retained: 3/7})
            self.assertEqual(result['domains']['GS']['excluded_techniques'], [ignored])
            self.assertEqual(result['weighted_coverage'], .55)

    def test_missing_modules_do_not_zero_fill_or_hide_observed_score(self):
        result = grade_level({'FS05': 83}, target_level=5)
        self.assertEqual(result['overall_score'], 83)
        self.assertEqual(result['observed_module_weight'], .14)
        self.assertEqual(result['domains']['GS']['score'], None)
        self.assertLess(result['weighted_coverage'], .14)
        self.assertEqual(result['gatekeepers']['intermediate']['status'], 'pending')
        self.assertEqual(result['eligible_level'], 2)

    def test_unknown_subtype_keeps_family_score_without_fabricating_techniques(self):
        result = grade_level({}, target_level=2, domain_scores={'SS': 81.5})
        self.assertEqual(result['overall_score'], 81.5)
        self.assertEqual(result['domains']['SS']['family_score'], 81.5)
        self.assertEqual(result['domains']['SS']['score_source'], 'observed_family')
        self.assertEqual(result['domains']['SS']['measured_techniques'], [])
        self.assertEqual(result['weighted_coverage'], 0)
        tier1 = grade_level({}, target_level=1, domain_scores={'SS': 81.5})
        self.assertIsNone(tier1['overall_score'])
        self.assertEqual(tier1['domains']['SS']['score'], 81.5)

    def test_family_score_not_double_counted_with_resolved_subtype(self):
        result = grade_level({'SS01': 50}, target_level=2, domain_scores={'SS': 90})
        self.assertEqual(result['overall_score'], 50)
        self.assertEqual(result['domains']['SS']['score_source'], 'measured_techniques')

    def test_gates_cap_level_never_numeric_score(self):
        all_gates = {key: True for keys in GATES.values() for key in keys}
        for failed, eligible in [('non_dominant_hand_ok', 2), ('serve_fluency_ok', 3), (None, 5)]:
            gates = dict(all_gates)
            if failed:
                gates[failed] = False
            result = grade_level({'GS01': 94}, target_level=5, gates=gates)
            self.assertEqual(result['overall_score'], 94)
            self.assertEqual(result['eligible_level'], eligible)
        result = grade_level({'GS01': 94}, target_level=5, gates={key: True for key in GATES['intermediate']})
        self.assertEqual(result['eligible_level'], 3)
        self.assertEqual(result['gatekeepers']['advanced']['status'], 'pending')

    def test_configuration_override_affects_subtechniques_not_major_weights(self):
        result = grade_level({'FS01': 20, 'FS05': 100}, technique_weights={'FS': {'FS01': 1, 'FS05': 3}})
        self.assertEqual(result['overall_score'], 80)
        self.assertEqual(result['weight_source'], 'caller_override')
        self.assertEqual(result['domains']['FS']['module_weight'], .3)

    def test_mixed_domains_follow_requested_level(self):
        a = grade_level({'GS01': 100, 'FS05': 0}, target_level=1)
        b = grade_level({'GS01': 100, 'FS05': 0}, target_level=5)
        self.assertEqual(a['overall_score'], 70)
        self.assertEqual(b['overall_score'], 56.25)

    def test_invalid_or_ambiguous_inputs_fail_explicitly(self):
        cases = [({'GS01': math.nan}, {}), ({'GS01': True}, {}), ({'GS01': 101}, {}), ({'GS99': 80}, {}),
                 ({'GS01': 1, 'GS-01': 2}, {}), ({}, {'target_level': True}), ({}, {'target_level': 6}),
                 ({}, {'backhand_style': 'ambidextrous'}), ({}, {'gates': {'serve_fluency_ok': 1}}),
                 ({}, {'gates': {'invented': True}}), ({}, {'domain_scores': {'SS': math.inf}}),
                 ({}, {'technique_weights': {'GS': {'GS03': 1}}}), ({}, {'technique_weights': {'GS': {'FS01': 1}}})]
        for scores, options in cases:
            with self.subTest(scores=scores, options=options), self.assertRaises(ValueError):
                grade_level(scores, **options)

    def test_empty_input_never_claims_level_or_score(self):
        result = grade_level({}, target_level=5)
        self.assertIsNone(result['overall_score'])
        self.assertIsNone(result['eligible_level'])
        self.assertEqual(result['status'], 'no_measured_active_techniques')


if __name__ == '__main__':
    unittest.main()
