# -*- coding: utf-8 -*-
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import fill_hourly_gaps as f

H = 3600 * 1000000   # 기준 시각(정시)


def rows(n, t=2.0, step=0.01, start=H, out=True):
    return [(start + i * 60, {"t1": "24.0", "h1": "35", "t2": "25.0", "h2": "36",
                              "to": ("%.2f" % (t + i * step)) if out else "", "ho": "70"}) for i in range(n)]


class Agg(unittest.TestCase):
    def test_hour_minmax_avg(self):
        a = f.aggregate(rows(60))[(H, "바닥외부")]
        self.assertEqual(a["n"], 60)
        self.assertAlmostEqual(a["minT"], 2.0); self.assertAlmostEqual(a["maxT"], 2.59)
        self.assertEqual(a["maxH"], 70)

    def test_too_few_skipped(self):
        self.assertNotIn((H, "바닥외부"), f.aggregate(rows(20)))

    def test_frozen_values_skipped(self):   # 게이트웨이 멈춤 → latest.json 굳음 → 1층·다락 값이 60번 같음
        a = f.aggregate(rows(60))
        self.assertNotIn((H, "내부1층"), a)
        self.assertIn((H, "바닥외부"), a)

    def test_empty_sensor_skipped(self):
        self.assertNotIn((H, "바닥외부"), f.aggregate(rows(60, out=False)))


class Cand(unittest.TestCase):
    def test_existing_and_recent_excluded(self):
        agg = f.aggregate(rows(60) + rows(60, start=H + 3600) + rows(60, start=H + 7200))
        now = H + 3 * 3600 + 10 * 60          # 세 번째 시간은 끝난 지 10분 → 아직
        got = f.candidates(agg, {(H, "바닥외부")}, now)
        self.assertEqual(got, [(H + 3600, "바닥외부")])


if __name__ == "__main__":
    unittest.main()
