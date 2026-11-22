# -*- coding: utf-8 -*-
import os, sys, time, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import heater_control as hc

P2 = hc.p2_cfg({"preheat": {}})


def ts(d, h, m=0):
    return time.mktime((2026, 11, d, h, m, 0, 0, 0, -1))


def hourly(d0, h0, n, sky=1, tmp=0.0, pty=0):
    out = []
    for i in range(n):
        t = ts(d0, h0) + i * 3600
        out.append({"ms": t * 1000, "tmp": tmp, "sky": sky, "pty": pty})
    return out


class Solar(unittest.TestCase):
    def test_curve(self):
        self.assertEqual(hc.solar_clear_w(5.0), 0.0)
        self.assertEqual(hc.solar_clear_w(18.0), 0.0)
        self.assertAlmostEqual(hc.solar_clear_w(11.5), 1650.0)
        self.assertTrue(500 < hc.solar_clear_w(9.0) < 1100)

    def test_sky(self):
        hr = hourly(22, 0, 24, sky=1)
        self.assertAlmostEqual(hc.solar_w(ts(22, 11, 30), hr), 1650.0)
        self.assertAlmostEqual(hc.solar_w(ts(22, 11, 30), hourly(22, 0, 24, sky=4)), 1650.0 * 0.15)
        self.assertAlmostEqual(hc.solar_w(ts(22, 11, 30), hourly(22, 0, 24, sky=1, pty=1)), 165.0)
        self.assertEqual(hc.solar_w(ts(22, 11, 30), []), 0.0)          # 예보 없음 → 0
        self.assertEqual(hc.solar_w(ts(22, 2, 0), hr), 0.0)            # 밤

    def test_start_later_when_sunny(self):
        # 11-22 재현: 21:00 공기 25°C, 바깥 0°C, 13:30 도착 25°C, 메인 1800 + 보조 500
        now, arr = ts(21, 21), ts(22, 13, 30)
        sunny = hourly(21, 21, 18, sky=1); cloudy = hourly(21, 21, 18, sky=4)
        s_sun, ta_sun = hc.solar_start(P2, 25.0, 25.0, now, arr, 25.0, 1800, 500, sunny, 0.0, 12)
        s_cld, ta_cld = hc.solar_start(P2, 25.0, 25.0, now, arr, 25.0, 1800, 500, cloudy, 0.0, 12)
        self.assertGreater(s_sun, s_cld)                # 맑으면 더 늦게 시작
        self.assertGreaterEqual(ta_sun, 25.0)
        self.assertGreaterEqual(ta_cld, 25.0)
        self.assertGreater(s_sun, ts(22, 1, 30))        # 실제(01:30)보다 늦게

    def test_unreachable_returns_earliest(self):
        now, arr = ts(22, 12), ts(22, 13)
        s, ta = hc.solar_start(P2, 5.0, 5.0, now, arr, 25.0, 1800, 0, [], -5.0, 12)
        self.assertEqual(s, now)
        self.assertLess(ta, 25.0)

    def test_past_goal(self):
        now = ts(22, 13, 20)
        s, ta = hc.solar_start(P2, 20.0, 20.0, now, ts(22, 13, 30), 25.0, 1800, 0, [], 0.0, 12)
        self.assertEqual(s, now)


if __name__ == "__main__":
    unittest.main()
