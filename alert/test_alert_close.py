# -*- coding: utf-8 -*-
"""닫기 경고: 닫기선 + 15분 하락 두 조건(11-20 설정 25/25.5 오경보 재현)."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import alert_sound as a


def hist(vals, now, step=60):
    n = len(vals)
    return [(now - (n - 1 - i) * step, v) for i, v in enumerate(vals)]


class Close(unittest.TestCase):
    def test_line(self):
        self.assertEqual(a.close_line(25.0, 25.5), 24.5)   # 설정이 25 근처 → 켜기 − 0.5
        self.assertEqual(a.close_line(24.0, 24.5), 23.5)
        self.assertEqual(a.close_line(23.0, 23.5), 25.0)   # 낮은 설정 → 예전처럼 25
        self.assertEqual(a.close_line(None, None), 25.0)

    def test_1120_natural_cooling_silent(self):
        # 20:56 25.5 → 21:10 24.9 (히터 꺼진 뒤 자연 냉각, 설정 25/25.5)
        now = 10000.0
        f = hist([25.5] * 5 + [25.4, 25.3, 25.3, 25.2, 25.2, 25.2, 25.2, 25.1, 25.0, 24.9, 24.9], now)
        self.assertFalse(a.close_fire(24.9, 25.0, 25.5, f, now))

    def test_door_open_fires(self):
        now = 10000.0
        f = hist([25.5, 25.4, 25.3, 25.1, 25.0, 24.8, 24.7, 24.6, 24.5, 24.4, 24.3, 24.2, 24.1, 24.0, 23.9, 23.8], now)
        self.assertTrue(a.close_fire(23.8, 25.0, 25.5, f, now))

    def test_slow_below_line_silent(self):   # 선 아래여도 천천히 식으면 조용
        now = 10000.0
        f = hist([24.6 - 0.03 * i for i in range(16)], now)
        self.assertFalse(a.close_fire(f[-1][1], 25.0, 25.5, f, now))

    def test_low_setpoint_fast_drop_at_25(self):   # 설정 23이면 25 아래 + 빠른 하락에 울림(예전 동작 유지)
        now = 10000.0
        f = hist([26.0 - 0.08 * i for i in range(16)], now)
        self.assertTrue(a.close_fire(f[-1][1], 23.0, 23.5, f, now))

    def test_no_history_silent(self):
        self.assertFalse(a.close_fire(24.0, 25.0, 25.5, [], 10000.0))


if __name__ == "__main__":
    unittest.main()
