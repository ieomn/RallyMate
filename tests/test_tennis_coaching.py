import unittest

from rallymate_service.tennis_coaching import coaching_for_metric, strength_for_metric


class TennisCoachingTests(unittest.TestCase):
    def test_different_weak_subcriteria_do_not_receive_the_same_advice(self):
        knee = coaching_for_metric("knee_load", 4, score=20)
        support = coaching_for_metric("support_control", .4, score=20)
        self.assertNotEqual(knee["action"], support["action"])
        self.assertIn("屈膝较少", knee["observation"])
        self.assertIn("调整", support["action"])
        self.assertIn("站距", support["title"])
        self.assertNotIn("髋部", support["observation"])
        self.assertIn("8", knee["drill"])
        self.assertTrue(knee["retest"])

    def test_existing_good_execution_is_not_called_a_defect(self):
        good = coaching_for_metric("arm_space", .85, score=95)
        self.assertIn("保持了舒展空间", good["observation"])
        self.assertNotIn("偏小", good["observation"])
        self.assertTrue(good["title"].startswith("巩固："))
        self.assertIn("85%", good["observation"])
        self.assertIn("至少 6 次", good["retest"])
        self.assertNotIn("减少手臂挤", good["retest"])

    def test_positive_observations_require_a_measured_high_score(self):
        self.assertIsNone(strength_for_metric("arm_space", .85, score=30))
        self.assertIsNone(strength_for_metric("arm_space", float("nan"), score=95))
        self.assertIsNone(strength_for_metric("unknown", .85, score=95))
        self.assertIsNone(strength_for_metric("arm_space", .85, score=101))
        good = strength_for_metric("preparation_sync", .05, score=95)
        self.assertIn("时间较接近", good["observation"])
        self.assertIn("5%", good["observation"])
        self.assertIn("综合本次已测动作", good["observation"])
        self.assertIn("测量汇总", good["observation"])

    def test_multi_component_metric_does_not_invent_a_missing_component_defect(self):
        follow = coaching_for_metric("follow_path", 1.2, score=30)
        self.assertIn("1.20 倍", follow["observation"])
        self.assertNotIn("延展较少", follow["observation"])
        self.assertNotIn("触球", follow["observation"])

    def test_continuity_coaching_is_specific_without_inventing_force_or_a_reversal(self):
        for metric in ("leg_extension_continuity", "follow_deceleration"):
            low = coaching_for_metric(metric, 0, score=0)
            high = coaching_for_metric(metric, .9, score=90)
            self.assertIn("0%", low["observation"])
            self.assertIn("90%", high["observation"])
            self.assertIn("8", low["drill"])
            self.assertIn("6 次", low["retest"])
            self.assertNotIn("反复", low["observation"], "zero can mean no net extension/deceleration")
            self.assertNotIn("发力", "".join(low.values()))
            self.assertTrue(high["title"].startswith("巩固："))

    def test_observations_follow_the_actual_phase_and_noise_floor_semantics(self):
        coil = coaching_for_metric("coil", 14, score=50)
        self.assertIn("准备后半段", coil["observation"])
        # A low positive ratio can be entirely monotonic but smaller than the
        # noise-scale denominator. It does not prove repeated reversals.
        small = coaching_for_metric("leg_extension_continuity", .2, score=20)
        self.assertNotIn("往返", small["observation"])
        self.assertIn("微小角度变化", small["observation"])
        follow = coaching_for_metric("follow_deceleration", 1, score=100)
        self.assertIn("首次恢复前", follow["observation"])
        self.assertIn("这段动作中", follow["observation"])
        self.assertNotIn("测量汇总", follow["observation"])

    def test_serve_uses_serve_specific_drills_instead_of_groundstroke_footwork(self):
        support = coaching_for_metric("support_control", .4, family="serve", score=20)
        self.assertIn("发球空挥", support["drill"])
        self.assertNotIn("喂慢球", support["drill"])
        trophy = coaching_for_metric("serve_trophy", 145, family="serve", score=20)
        self.assertIn("持拍臂", trophy["title"])
        self.assertNotIn("转体蓄势较少", trophy["observation"])

    def test_recovery_advice_allows_natural_continuous_turning(self):
        advice = coaching_for_metric("recovery_control", 1.5, score=30)
        self.assertIn("自然转身", advice["action"])
        self.assertIn("不必先停住", advice["action"])
        self.assertNotIn("收敛", advice["observation"])

    def test_general_guidance_never_claims_unmeasured_contact_or_physical_force(self):
        for name in ("release_sequence", "coil_release", "head_control", "follow_path"):
            with self.subTest(metric=name):
                result = coaching_for_metric(name, .2, family="serve", score=30)
                text = "".join(result.values())
                for forbidden in ("没有盯球", "击球点错误", "拍面错误", "实际受力", "原文", "待复核"):
                    self.assertNotIn(forbidden, text)
                self.assertEqual(set(result), {"title", "observation", "action", "drill", "retest"})


if __name__ == "__main__":
    unittest.main()
