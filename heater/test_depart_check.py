# -*- coding: utf-8 -*-
import os, sys, time, datetime, unittest
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import depart_check as dc


def ts(y, m, d, h=0):
    return time.mktime(datetime.datetime(y, m, d, h).timetuple())


class ForecastMin(unittest.TestCase):
    def setUp(self):
        self.now = ts(2026, 11, 19, 10)
        self.series = [(ts(2026, 11, 19, 10) + i * 3600, 5 - i * 0.2) for i in range(48)]  # 최저 약 -4.4 at +47h

    def test_short_term_min(self):
        t, when, src = dc.forecast_min(self.now, self.series, [], days=7)
        self.assertAlmostEqual(t, 5 - 47 * 0.2)
        self.assertEqual(src, "단기")

    def test_mid_colder_beyond_short(self):
        mid = [["2026-11-24", "", "", "", "", "-7", "5", ""]]
        t, when, src = dc.forecast_min(self.now, self.series, mid, days=7)
        self.assertEqual((t, when, src), (-7.0, "11-24", "중기"))

    def test_mid_ignored_when_covered_or_beyond(self):
        mid = [["2026-11-19", "", "", "", "", "-20", "", ""],   # 단기예보가 덮는 날
               ["2026-12-01", "", "", "", "", "-20", "", ""],   # 7일 밖
               ["2026-11-23", "", "", "", "", "", "", ""]]      # 값 없음
        t, _, src = dc.forecast_min(self.now, self.series, mid, days=7)
        self.assertEqual(src, "단기")

    def test_no_data(self):
        self.assertEqual(dc.forecast_min(self.now, [], []), (None, None, None))


class Message(unittest.TestCase):
    def test_threshold(self):
        self.assertIsNone(dc.message(3.0, "11-21 06시", 2.0))
        self.assertIsNone(dc.message(None, None))
        m = dc.message(-4, "11-21 06시", 2.0)
        self.assertIn("-4°C(11-21 06시)", m)
        self.assertIn("P트랩", m)

    def test_speech(self):
        s = dc.speech(-4)
        self.assertIn("영하 4도", s)
        self.assertNotIn("(", s)


if __name__ == "__main__":
    unittest.main()
