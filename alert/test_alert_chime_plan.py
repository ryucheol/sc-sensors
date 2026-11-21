# -*- coding: utf-8 -*-
"""시보 미리 시작(캐스트 지연 보정) 계획."""
import datetime as D, os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import alert_sound as a


class Plan(unittest.TestCase):
    def test_wakes_lead_before_boundary(self):
        p = a.chime_plan(D.datetime(2026, 11, 21, 9, 59, 40), "comfort", 10, 22)
        self.assertEqual(p[0], "202611211000")
        self.assertEqual(p[1], D.datetime(2026, 11, 21, 10, 0))
        self.assertAlmostEqual(p[2], 10.0)

    def test_inside_lead_says_now(self):
        p = a.chime_plan(D.datetime(2026, 11, 21, 10, 29, 55), "comfort", 10, 22)
        self.assertEqual(p[0], "202611211030"); self.assertEqual(p[2], 0.0)

    def test_too_early_or_other_mode(self):
        self.assertIsNone(a.chime_plan(D.datetime(2026, 11, 21, 9, 58, 0), "comfort", 10, 22))
        self.assertIsNone(a.chime_plan(D.datetime(2026, 11, 21, 9, 59, 50), "sleep", 10, 22))
        self.assertIsNone(a.chime_plan(D.datetime(2026, 11, 21, 6, 59, 50), "comfort", 10, 22) and None)

    def test_hours(self):
        self.assertEqual(a.chime_plan(D.datetime(2026, 11, 21, 6, 59, 50), "comfort", 10, 22)[0], "202611210700")  # 07시 첫 시보
        self.assertIsNone(a.chime_plan(D.datetime(2026, 11, 21, 22, 59, 50), "comfort", 10, 22))                 # 23시는 범위 밖


if __name__ == "__main__":
    unittest.main()
