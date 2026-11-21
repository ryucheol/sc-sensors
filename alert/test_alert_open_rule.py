# -*- coding: utf-8 -*-
"""열기 경고: 겨울 햇빛(바깥 < 10°C + 히터 꺼짐)이면 28°C·30분·부드러운 문구."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import alert_sound as a


class OpenRule(unittest.TestCase):
    def test_winter_sun(self):          # 11-21 10:50: 바깥 3°C, 히터 꺼짐, 1층 26.4 → 아직 조용
        on_t, rep, ph1, _ = a.open_rule(3.0, False)
        self.assertEqual((on_t, rep), (28.0, 1800)); self.assertIn("다락 창", ph1)

    def test_heater_on_keeps_26(self):  # 히터가 켜진 채 26°C↑ = 제어 이상 가능 → 예전대로
        self.assertEqual(a.open_rule(3.0, True)[:2], (26.0, 300))

    def test_warm_outside_or_unknown_keeps_26(self):
        self.assertEqual(a.open_rule(15.0, False)[:2], (26.0, 300))
        self.assertEqual(a.open_rule(None, False)[:2], (26.0, 300))
        self.assertEqual(a.open_rule(3.0, None)[:2], (26.0, 300))   # 히터 상태 모름 → 보수적으로 26


if __name__ == "__main__":
    unittest.main()
