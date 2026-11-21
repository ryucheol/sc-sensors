# -*- coding: utf-8 -*-
import os, sys, time, unittest
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import passive_gateway as g


class HeaterRow(unittest.TestCase):
    def test_sleep_cmd_backstop(self):
        t0 = time.mktime((2026, 11, 20, 23, 10, 0, 0, 0, -1))
        r = g.heater_row({"mode": "sleep", "on": 25.0, "off": 26.0, "source": "cmd", "since": t0, "last_cmd_ts": t0},
                         {"relay": False}, 36, t0 + 60)
        self.assertEqual(r["sensor"], "히터")
        self.assertEqual(r["temp"], "🌙 취침 25/26 · 11-20 23:10부터 · 백스톱 11-22 11:10")
        self.assertEqual(r["hum"], "OFF")
        self.assertEqual(r["volt"], "")

    def test_frost_arrival(self):
        t0 = time.mktime((2026, 11, 21, 10, 0, 0, 0, 0, -1))
        r = g.heater_row({"mode": "frost", "on": 3, "off": 5, "source": "cmd", "since": t0 - 86400,
                          "arrival": {"ts": t0, "target": 23.5}},
                         {"relay": True, "watt": 1812, "arrival_start_pred": t0 - 36000}, 36, t0 - 40000)
        self.assertNotIn("백스톱", r["temp"])
        self.assertEqual(r["hum"], "ON 1812W")
        self.assertEqual(r["volt"], "11-21 10:00 23.5°C · 예상 시작 11-21 00:00")

    def test_fan(self):
        t0 = time.mktime((2026, 11, 21, 13, 47, 0, 0, 0, -1))
        up = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0))
        st = {"mode": "comfort", "on": 23, "off": 23.5}
        r = g.heater_row(st, {"relay": False}, 36, t0 + 60, {"updated": up, "on": True, "why": "자동: 다락−1층 1.7°C"})
        self.assertEqual(r["hum"], "OFF · 선풍기 ON")
        r = g.heater_row(st, {"relay": False}, 36, t0 + 60, {"updated": up, "on": False, "why": "수동 끄기(4시간)"})
        self.assertEqual(r["hum"], "OFF · 선풍기 OFF(수동)")
        r = g.heater_row(st, {"relay": False}, 36, t0 + 3600, {"updated": up, "on": True})
        self.assertEqual(r["hum"], "OFF · 선풍기 ?")
        self.assertEqual(g.heater_row(st, {"relay": False}, 36, t0)["hum"], "OFF")

    def test_empty(self):
        self.assertIsNone(g.heater_row({}, {}, 36, 0))


if __name__ == "__main__":
    unittest.main()
