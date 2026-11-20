# -*- coding: utf-8 -*-
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "heater"))
import qingping_poll as q
import passive_gateway as g


def rec(ts, co2=800):
    return {"timestamp": {"value": ts}, "temperature": {"value": 22.5}, "humidity": {"value": 40},
            "co2": {"value": co2}, "battery": {"value": 100}}


class Rows(unittest.TestCase):
    def test_dedup_sort_after(self):
        d = [rec(300), rec(100), rec(200), rec(200), {"co2": {"value": 1}}]
        self.assertEqual([r[0] for r in q.to_rows(d, 100)], [200, 300])
        self.assertEqual(q.to_rows(d, 0)[0], (100, 22.5, 40, 800, 100))

    def test_window(self):
        self.assertEqual(q.window(0, 10000), (10000 - 3600, 10000))           # 처음: 1시간
        self.assertEqual(q.window(9000, 10000), (9001, 10000))                # 이어서
        self.assertEqual(q.window(1, 10 ** 6), (10 ** 6 - 2 * 86400, 10 ** 6))   # 오래 끊김: 2일까지


class SheetRow(unittest.TestCase):
    def test_fresh_and_stale(self):
        r = g.qingping_row({"ts": 1000, "temp": 21.0, "hum": 45, "co2": 950}, 1000 + 600)
        self.assertEqual((r["sensor"], r["temp"], r["hum"], r["volt"]), ("CO2(Qingping)", 21.0, 45, 950))
        self.assertIsNone(g.qingping_row({"ts": 1000, "co2": 950}, 1000 + 3600))
        self.assertIsNone(g.qingping_row(None, 0))


if __name__ == "__main__":
    unittest.main()
