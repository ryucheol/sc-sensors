#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""data_watch 테스트(동글·네트워크 불필요).  실행: cd notify && python3 -m unittest -v test_data_watch"""
import os, sys, json, tempfile, datetime, unittest
from unittest import mock
os.environ["SMART_CABIN_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import data_watch as dw   # noqa: E402
GB, MB = dw.GB, dw.MB


class Pure(unittest.TestCase):
    def test_cycle_start(self):
        D = datetime.date
        self.assertEqual(dw.cycle_start(D(2026, 11, 13), 1), D(2026, 11, 1))
        self.assertEqual(dw.cycle_start(D(2026, 11, 13), 20), D(2026, 10, 20))
        self.assertEqual(dw.cycle_start(D(2026, 3, 5), 31), D(2026, 2, 28))

    def test_xml(self):
        x = "<response><CurrentMonthDownload>1073741824</CurrentMonthDownload><CurrentMonthUpload>5</CurrentMonthUpload></response>"
        self.assertEqual(dw.xml_val(x, "CurrentMonthDownload"), "1073741824"); self.assertIsNone(dw.xml_val(x, "Nope"))

    def test_tally_survives_counter_reset(self):
        st = {}
        self.assertEqual(dw.tally(st, 1000, "c1"), 0)
        self.assertEqual(dw.tally(st, 1500, "c1"), 500)
        self.assertEqual(dw.tally(st, 200, "c1"), 500)        # 재부팅으로 카운터가 줄면 새 기준
        self.assertEqual(dw.tally(st, 700, "c1"), 1000)
        self.assertEqual(dw.tally(st, 900, "c2"), 0)          # 새 주기 → 0부터

    def test_levels_once(self):
        self.assertEqual(dw.levels_to_alert(3.1 * GB, 6 * GB, [0.5, 0.7], []), [0.5])
        self.assertEqual(dw.levels_to_alert(4.3 * GB, 6 * GB, [0.5, 0.7], ["0.5"]), [0.7])

    def test_burst(self):
        h = []
        dw.burst(h, 0, 0); dw.burst(h, 1800, 50 * MB)
        self.assertEqual(dw.burst(h, 3700, 300 * MB), 300 * MB)


class Run(unittest.TestCase):
    def setUp(self):
        for p in (dw.STATE, dw.OUT):
            if os.path.exists(p): os.remove(p)
        self.sent = []
        self.p = [mock.patch.object(dw.tg, "send", side_effect=lambda m, **k: self.sent.append((k.get("key"), m))),
                  mock.patch.object(dw, "wan_counter", return_value=("usb0", 10))]
        for x in self.p: x.start()

    def tearDown(self):
        for x in self.p: x.stop()

    def test_hilink_alerts_once(self):
        with mock.patch.object(dw, "hilink_month", return_value=int(3.2 * GB)):
            dw.run(now=1794600000); dw.run(now=1794600600)
        keys = [k for k, _ in self.sent]
        self.assertEqual(keys.count("data_lv_0.5"), 1)
        out = json.load(open(dw.OUT)); self.assertEqual(out["source"], "hilink"); self.assertEqual(out["pct"], 53.3)

    def test_fallback_iface(self):
        with mock.patch.object(dw, "hilink_month", return_value=None):
            dw.run(now=1794600000)
        self.assertEqual(json.load(open(dw.OUT))["source"], "iface:usb0")

    def test_burst_alert(self):
        with mock.patch.object(dw, "hilink_month", side_effect=[int(1 * GB), int(1 * GB) + 300 * MB]):
            dw.run(now=1794600000); dw.run(now=1794603700)
        self.assertIn("data_burst", [k for k, _ in self.sent])


if __name__ == "__main__":
    unittest.main()
