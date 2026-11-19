# -*- coding: utf-8 -*-
import os, sys, json, time, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import sheet_cmd_poll as scp


class PreheatStarted(unittest.TestCase):
    def test_started(self):
        self.assertEqual(scp.preheat_started({"mode": "comfort", "source": "arrival", "since": 123.0}), 123.0)
        self.assertEqual(scp.preheat_started({"mode": "comfort", "source": "preheat", "since": 5}), 5)

    def test_not_started(self):
        self.assertIsNone(scp.preheat_started({"mode": "frost", "source": "cmd", "since": 1}))
        self.assertIsNone(scp.preheat_started({"mode": "comfort", "source": "cmd", "since": 1}))   # 사람이 '난방'
        self.assertIsNone(scp.preheat_started({}))
        self.assertIsNone(scp.preheat_started(None))


class StatusText(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.old = scp.BASE; scp.BASE = self.d
        self.arr = time.time() + 3 * 3600

    def tearDown(self):
        scp.BASE = self.old

    def write(self, state, status):
        json.dump(state, open(os.path.join(self.d, "heater_state.json"), "w"))
        json.dump(status, open(os.path.join(self.d, "heater_status.json"), "w"))

    def test_shows_started_instead_of_estimate(self):
        since = time.time() - 6 * 3600
        self.write({"mode": "comfort", "on": 23.5, "off": 24, "source": "arrival", "since": since},
                   {"relay": True, "arrival": {"ts": self.arr}, "arrival_start_est": time.time() + 600})
        t = scp.status_text()
        self.assertIn("예열 중(%s 시작)" % time.strftime("%m-%d %H:%M", time.localtime(since)), t)
        self.assertNotIn("예상시작", t)

    def test_shows_estimate_before_start(self):
        self.write({"mode": "frost", "on": 3, "off": 5, "source": "cmd", "since": 1},
                   {"relay": False, "arrival": {"ts": self.arr}, "arrival_start_est": time.time() + 600})
        self.assertIn("예상시작", scp.status_text())


if __name__ == "__main__":
    unittest.main()
