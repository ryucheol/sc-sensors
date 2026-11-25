# -*- coding: utf-8 -*-
"""저전압 감시 테스트.  실행: cd notify && python3 -m unittest test_power_watch"""
import os, sys, tempfile, unittest, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import power_watch as pw

L1 = "2026-11-20T23:12:34+0900 smartcabin kernel: hwmon hwmon1: Undervoltage detected!"
L2 = "2026-11-20T23:12:40+0900 smartcabin kernel: hwmon hwmon1: Voltage normalised"


class Parse(unittest.TestCase):
    def test_lines(self):
        self.assertEqual(pw.parse_line(L1), ("under", dt.datetime(2026, 11, 20, 23, 12, 34)))
        self.assertEqual(pw.parse_line(L2)[0], "normal")
        self.assertIsNone(pw.parse_line("2026-11-20T23:12:40+0900 smartcabin kernel: usb 1-1.3: new device"))


class Track(unittest.TestCase):
    def tracker(self):
        return pw.Tracker(snapshot=lambda: ("켜짐", 1780, "comfort"), usb=lambda: "HUAWEI E3372")

    def test_pair_and_duration(self):
        t = self.tracker()
        self.assertIsNone(t.feed(*pw.parse_line(L1)))
        r = t.feed(*pw.parse_line(L2))
        self.assertEqual(r["dur_s"], 6); self.assertEqual(r["heater"], "켜짐"); self.assertEqual(r["usb"], "HUAWEI E3372")

    def test_under_twice_closes_previous(self):
        t = self.tracker()
        t.feed("under", dt.datetime(2026, 11, 20, 23, 0))
        r = t.feed("under", dt.datetime(2026, 11, 20, 23, 5))
        self.assertEqual(r["dur_s"], ""); self.assertEqual(r["end"], "")
        self.assertIsNone(t.feed("normal", dt.datetime(2026, 11, 20, 23, 6)) and None)

    def test_stray_normal_ignored(self):
        self.assertIsNone(self.tracker().feed("normal", dt.datetime(2026, 11, 20)))

    def test_alert_throttle_and_pending_count(self):
        t = self.tracker()
        row = {"dur_s": 6, "heater": "켜짐", "watt": 1780, "usb": "x"}
        self.assertIn("오늘 1번째", t.message(row, 1, 1000.0))
        self.assertIsNone(t.message(row, 2, 1100.0))
        m = t.message(row, 3, 1000.0 + pw.ALERT_EVERY_S + 1)
        self.assertIn("지난 알림 뒤 2번", m); self.assertIn("1780W", m)

    def test_csv_and_today_count(self):
        d = tempfile.mkdtemp(); p = os.path.join(d, "p.csv")
        for s in ("2026-11-20 23:12:34", "2026-11-21 00:13:00"):
            pw.append({"start": s, "end": "", "dur_s": "", "heater": "", "watt": 0, "mode": "", "usb": ""}, p)
        self.assertEqual(pw.today_count(p, dt.date(2026, 11, 20)), 1)


class Alarm(unittest.TestCase):
    def test_find_rpi_volt(self):
        d = tempfile.mkdtemp()
        for n, name in (("hwmon0", "cpu_thermal"), ("hwmon1", "rpi_volt")):
            os.makedirs(os.path.join(d, n))
            open(os.path.join(d, n, "name"), "w").write(name + "\n")
        open(os.path.join(d, "hwmon1", "in0_lcrit_alarm"), "w").write("0\n")
        self.assertTrue(pw.find_alarm(d).endswith("hwmon1/in0_lcrit_alarm"))
        self.assertIsNone(pw.find_alarm(os.path.join(d, "none")))


if __name__ == "__main__":
    unittest.main()
