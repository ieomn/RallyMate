import unittest

from rallymate_service.tennis_coaching import coaching_for_metric


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
        self.assertIn("已较好完成", good["observation"])
        self.assertNotIn("偏小", good["observation"])

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
