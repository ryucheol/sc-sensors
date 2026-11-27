#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sheet_summary.hourly·day_csv_gz 단위 테스트(표준 라이브러리). 실행: cd gateway && python3 -m unittest test_sheet_summary"""
import datetime as dt, gzip, os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import sheet_summary as ss

HDR = ["ts", "mode", "relay", "watt", "volt", "t1", "h1", "t2", "h2", "to", "ho", "t_ctl", "delta", "fan"]


def mk(t, relay=1, watt=1000, t1=20.0, fan=0, mode="comfort"):
    r = dict(zip(HDR, [t.strftime("%Y-%m-%d %H:%M:%S"), mode, str(relay), str(watt), "220", str(t1), "50", "21", "48", "5", "80", str(t1), "1.0", str(fan)]))
    r["_t"] = t
    return r


class Hourly(unittest.TestCase):
    def setUp(self):
        self.t0 = dt.datetime(2026, 11, 15, 10, 0, 0)

    def test_full_hour_on(self):
        rows = [mk(self.t0 + dt.timedelta(minutes=m)) for m in range(60)] + [mk(self.t0 + dt.timedelta(hours=1))]
        out = ss.hourly(rows, None, self.t0 + dt.timedelta(hours=1))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][0], "2026-11-15 10:00")
        self.assertAlmostEqual(out[0][2], 1.0, places=3)      # 1000 W × 1 h = 1 kWh
        self.assertAlmostEqual(out[0][3], 60.0)
        self.assertEqual(out[0][4], 1000)
        self.assertEqual(out[0][13], 60)

    def test_half_on(self):
        rows = [mk(self.t0 + dt.timedelta(minutes=m), relay=1 if m < 30 else 0, watt=1750 if m < 30 else 0) for m in range(60)]
        rows.append(mk(self.t0 + dt.timedelta(hours=1)))
        out = ss.hourly(rows, None, self.t0 + dt.timedelta(hours=1))
        self.assertAlmostEqual(out[0][2], 0.875, places=3)    # 1750 W × 0.5 h
        self.assertAlmostEqual(out[0][3], 30.0)
        self.assertEqual(out[0][4], 1750)

    def test_gap_capped(self):
        # 10:00 한 번, 다음 기록이 11:30 → 10:00 샘플은 5분만 대표
        rows = [mk(self.t0), mk(self.t0 + dt.timedelta(minutes=90))]
        out = ss.hourly(rows, None, self.t0 + dt.timedelta(hours=1))
        self.assertAlmostEqual(out[0][2], 1000 * 300 / 3600 / 1000, places=3)

    def test_excludes_current_hour_and_before_start(self):
        rows = [mk(self.t0 + dt.timedelta(minutes=m)) for m in range(0, 180, 10)]
        out = ss.hourly(rows, self.t0 + dt.timedelta(hours=1), self.t0 + dt.timedelta(hours=2))
        self.assertEqual([r[0] for r in out], ["2026-11-15 11:00"])

    def test_mode_majority_and_fan(self):
        rows = [mk(self.t0 + dt.timedelta(minutes=m), mode="frost" if m < 40 else "comfort", fan=1 if m < 15 else 0) for m in range(60)]
        rows.append(mk(self.t0 + dt.timedelta(hours=1)))
        out = ss.hourly(rows, None, self.t0 + dt.timedelta(hours=1))
        self.assertEqual(out[0][1], "frost")
        self.assertAlmostEqual(out[0][12], 15.0)


class Aux(unittest.TestCase):
    def test_aux_kwh_minutes_and_unknown_hour(self):
        t0 = dt.datetime(2026, 11, 28, 5, 0, 0)
        aux = [{"_t": t0 + dt.timedelta(minutes=m), "on": "1" if m < 30 else "0", "watt": "480" if m < 30 else "0"} for m in range(60)]
        aux.append({"_t": t0 + dt.timedelta(hours=1), "on": "0", "watt": "0"})
        h = ss.aux_hourly(aux, None, t0 + dt.timedelta(hours=2))
        self.assertEqual(h[t0], (0.24, 30.0))                 # 480 W × 0.5 h
        rows = [mk(t0 + dt.timedelta(minutes=m)) for m in range(120)] + [mk(t0 + dt.timedelta(hours=2))]
        summ = ss.add_aux(ss.hourly(rows, None, t0 + dt.timedelta(hours=2)), {t0: h[t0]})
        self.assertEqual(summ[0][-2:], [0.24, 30.0])
        self.assertEqual(summ[1][-2:], ["", ""])              # 보조 기록이 없는 시간 = 빈칸(0과 구분)
        self.assertEqual(len(summ[0]), 16)


class RawDay(unittest.TestCase):
    def test_gzip_day_only(self):
        d0 = dt.datetime(2026, 11, 14, 23, 58)
        rows = [mk(d0), mk(d0 + dt.timedelta(minutes=1)), mk(d0 + dt.timedelta(minutes=3))]
        txt = gzip.decompress(ss.day_csv_gz(rows, dt.date(2026, 11, 14), HDR)).decode()
        lines = txt.strip().split("\n")
        self.assertEqual(lines[0], ",".join(HDR))
        self.assertEqual(len(lines), 3)   # 머리글 + 23:58, 23:59 (00:01은 다음 날)


if __name__ == "__main__":
    unittest.main()
