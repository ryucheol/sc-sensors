# -*- coding: utf-8 -*-
"""실내 예측 기록장 테스트.  실행: cd heater && python3 -m unittest test_predict_log"""
import os, sys, json, tempfile, unittest, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import heater_control as hc
import predict_log as pl

P2 = dict(hc.PREHEAT2_DEFAULT)
T0 = dt.datetime(2026, 11, 24, 18, 5)


def const(v):
    return lambda ts: v


class Simulate(unittest.TestCase):
    def test_free_cooling_goes_down_and_no_heater(self):
        r = pl.simulate_night(P2, 10.0, 11.0, const(0.0), T0.timestamp(), (T0 + dt.timedelta(hours=13)).timestamp(), -99, -98, 0)
        self.assertLess(r["end_t"], 10.0); self.assertGreater(r["end_t"], 0.0)
        self.assertFalse(r["heater_on"]); self.assertEqual(r["heater_kwh"], 0.0)

    def test_11_24_night_close_to_actual(self):   # 11-24 18:05 1층 9.6·가구 ~11, 바깥(바닥외부) ≈ −1 → 실측 07시 3.2°C
        r = pl.simulate_night(P2, 9.6, 11.0, const(-0.8), T0.timestamp(), dt.datetime(2026, 11, 25, 7).timestamp(), -99, -98, 0)
        self.assertAlmostEqual(r["end_t"], 3.2, delta=1.0)

    def test_frost_hysteresis_turns_heater_on(self):
        r = pl.simulate_night(P2, 4.0, 4.0, const(-8.0), T0.timestamp(), (T0 + dt.timedelta(hours=10)).timestamp(), 3, 5, 1700)
        self.assertTrue(r["heater_on"]); self.assertGreater(r["heater_kwh"], 0.1)
        self.assertGreaterEqual(r["min_t"], 2.9)


class SeriesFn(unittest.TestCase):
    def test_interp_offset_and_edges(self):
        s = [(0.0, 0.0), (3600.0, -2.0)]
        f = pl.series_fn(s, 9.0)
        self.assertAlmostEqual(f(1800), -1.0 + pl.OUTDOOR_OFFSET)
        self.assertAlmostEqual(f(-10), 0.0 + pl.OUTDOOR_OFFSET)
        self.assertAlmostEqual(f(99999), -2.0 + pl.OUTDOOR_OFFSET)
        self.assertEqual(pl.series_fn([], 7.0)(0), 7.0)


class Actuals(unittest.TestCase):
    def rows(self, heater=False):
        out = []; t = dt.datetime(2026, 11, 24, 18, 0)
        v = 10.0
        while t <= dt.datetime(2026, 11, 25, 8, 0):
            out.append((t, v, heater and t.hour == 5, 1700.0 if heater and t.hour == 5 else 0.0))
            v -= 0.008; t += dt.timedelta(minutes=1)
        return out

    def test_min_and_target(self):
        a = pl.actuals(self.rows(), dt.datetime(2026, 11, 25, 7))
        self.assertEqual(a["act_min_at"], "08:00"); self.assertFalse(a["act_heater_on"]); self.assertEqual(a["act_heater_kwh"], 0.0)
        self.assertLess(a["act_t1_target"], 10.0)

    def test_heater_kwh(self):
        a = pl.actuals(self.rows(heater=True), dt.datetime(2026, 11, 25, 7))
        self.assertTrue(a["act_heater_on"]); self.assertAlmostEqual(a["act_heater_kwh"], 1.7, delta=0.05)

    def test_empty(self):
        self.assertIsNone(pl.actuals([], dt.datetime(2026, 11, 25, 7)))


class VerifyEndToEnd(unittest.TestCase):
    def test_fills_actuals_and_errors(self):
        d = tempfile.mkdtemp()
        log = os.path.join(d, "p.jsonl"); th = os.path.join(d, "t.csv")
        e = {"target": "2026-11-25 07:00", "made": "2026-11-24 18:05", "pred_t1_target": 3.5, "pred_min_t1": 3.0,
             "pred_free_min": 3.3}
        pl.write_log([e], log)
        with open(th, "w") as f:
            f.write("ts,mode,relay,watt,volt,t1,h1,t2,h2,to,ho,t_ctl,delta,fan\n")
            t = dt.datetime(2026, 11, 24, 18, 5); v = 9.6
            while t <= dt.datetime(2026, 11, 25, 8, 30):
                f.write("%s,frost,0,0,220,%.2f,50,%.2f,50,0,80,%.1f,0.1,0\n" % (t.strftime("%Y-%m-%d %H:%M:%S"), v, v + 0.3, v))
                v = max(3.2, v - 0.008); t += dt.timedelta(minutes=1)
        self.assertEqual(pl.verify(dt.datetime(2026, 11, 25, 7, 30), log, th), 0)   # 아직 08시 전
        self.assertEqual(pl.verify(dt.datetime(2026, 11, 25, 9, 10), log, th), 1)
        got = pl.read_log(log)[0]
        self.assertAlmostEqual(got["act_min_t1"], 3.2); self.assertAlmostEqual(got["err_free_min"], 0.1)
        self.assertIn("err_t1_target", got)
        self.assertEqual(pl.verify(dt.datetime(2026, 11, 25, 9, 20), log, th), 0)    # 이미 채움


class SheetRows(unittest.TestCase):
    def test_columns_and_bool(self):
        r = pl.sheet_rows([{"target": "2026-11-25 07:00", "pred_heater_on": True, "act_heater_on": False}])[0]
        self.assertEqual(len(r), len(pl.SHEET_COLS))
        self.assertEqual(r[pl.SHEET_COLS.index("pred_heater_on")], "켜짐")
        self.assertEqual(r[pl.SHEET_COLS.index("act_heater_on")], "꺼짐")
        self.assertEqual(r[pl.SHEET_COLS.index("arrival")], "")


if __name__ == "__main__":
    unittest.main()
