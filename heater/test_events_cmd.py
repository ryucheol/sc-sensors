# -*- coding: utf-8 -*-
"""조리·환기 명령(열모델 제외 구간 기록)."""
import datetime as D, os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import sheet_cmd_poll as s

NOW = D.datetime(2026, 11, 21, 13, 20).timestamp()


class Plan(unittest.TestCase):
    def test_default_and_minutes(self):
        self.assertEqual(s.event_plan("조리", [], NOW, []), ("add", NOW, NOW + 3600))
        self.assertEqual(s.event_plan("환기", [], NOW, []), ("add", NOW, NOW + 1200))
        self.assertEqual(s.event_plan("조리", ["40분"], NOW, []), ("add", NOW, NOW + 2400))
        self.assertEqual(s.event_plan("조리", ["40", "분"], NOW, []), ("add", NOW, NOW + 2400))

    def test_past_range(self):
        a = D.datetime(2026, 11, 21, 11, 50).timestamp(); b = D.datetime(2026, 11, 21, 13, 0).timestamp()
        self.assertEqual(s.event_plan("조리", ["11:50~13:00"], NOW, []), ("add", a, b))
        self.assertEqual(s.event_plan("환기", ["11:50", "13:00"], NOW, []), ("add", a, b))
        self.assertEqual(s.event_plan("환기", ["13:00~11:50"], NOW, [])[0], "err")

    def test_end(self):
        rows = [(NOW - 600, NOW + 3000, "조리", "")]
        self.assertEqual(s.event_plan("조리", ["끝"], NOW, rows), ("end", 0, NOW))
        self.assertEqual(s.event_plan("환기", ["끝"], NOW, rows)[0], "err")

    def test_roundtrip(self):
        p = os.path.join(tempfile.mkdtemp(), "e.csv")
        st, msg = s.event_cmd("조리", ["11:50~13:00"], NOW, p)
        self.assertEqual(st, "완료"); self.assertIn("11:50~13:00", msg)
        st, msg = s.event_cmd("환기", [], NOW, p)
        st, msg = s.event_cmd("환기", ["끝"], NOW + 300, p)
        self.assertIn("13:20~13:25", msg)
        self.assertEqual(len(s.load_events(p)), 2)


if __name__ == "__main__":
    unittest.main()
