#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wake_heat 단위 테스트. 실행: cd heater && python3 -m unittest test_wake_heat"""
import datetime as dt, os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import wake_heat as wh

W = {"enabled": True, "time": "06:00", "target": None}


class Due(unittest.TestCase):
    def test_window_and_mode(self):
        d = dt.datetime(2026, 11, 17, 6, 0, 30)
        self.assertTrue(wh.due(W, "sleep", d))
        self.assertTrue(wh.due(W, "sleep", d.replace(minute=29)))
        self.assertFalse(wh.due(W, "sleep", d.replace(minute=30)))
        self.assertFalse(wh.due(W, "sleep", d.replace(hour=5, minute=59)))
        self.assertFalse(wh.due(W, "frost", d))            # 무인이면 절대 안 켬
        self.assertFalse(wh.due(W, "comfort", d))
        self.assertFalse(wh.due(dict(W, enabled=False), "sleep", d))
        self.assertFalse(wh.due(dict(W, done="2026-11-17"), "sleep", d))


class Parse(unittest.TestCase):
    def test_parse(self):
        w, msg = wh.parse_cmd(["06:30"], dict(W, target=25)); self.assertEqual((w["time"], w["target"]), ("06:30", None))   # 온도 없으면 기본
        w, _ = wh.parse_cmd(["7:15", "24"], W); self.assertEqual((w["time"], w["target"]), ("07:15", 24.0))
        w, _ = wh.parse_cmd(["6시", "30분"], W); self.assertEqual(w["time"], "06:30")
        w, _ = wh.parse_cmd(["끔"], W); self.assertFalse(w["enabled"])
        w, msg = wh.parse_cmd([], W); self.assertIsNone(msg)
        with self.assertRaises(ValueError):
            wh.parse_cmd(["아무거나"], W)


if __name__ == "__main__":
    unittest.main()
