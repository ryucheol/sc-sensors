# -*- coding: utf-8 -*-
"""여러 컴퓨터가 같은 시계를 갱신할 때의 겹침 방지·글꼴 테스트.  실행: cd smartclock && python3 -m unittest test_smartclock"""
import os, sys, unittest
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import smartclock_push as sp

NOW = datetime(2026, 11, 25, 21, 30)


def info(*names):
    return {"files": [{"name": n, "size": 1, "enabled": True} for n in names] + [{"name": "girl.jpg", "size": 1}]}


class Coordination(unittest.TestCase):
    def test_parse_names_old_and_new(self):
        f = sp.clock_files(info("sc_1125_2127_abcdef.jpg", "sc_1125_2100.jpg"), NOW)
        self.assertEqual([(n, h) for n, _, h in f], [("sc_1125_2127_abcdef.jpg", "abcdef"), ("sc_1125_2100.jpg", None)])
        self.assertEqual(f[0][1], datetime(2026, 11, 25, 21, 27))

    def test_year_rollover(self):
        f = sp.clock_files(info("sc_1231_2358_abcdef.jpg"), datetime(2027, 1, 1, 0, 1))
        self.assertEqual(f[0][1].year, 2026)

    def test_recent_upload_by_other_machine_skips(self):
        f = sp.clock_files(info("sc_1125_2127_abcdef.jpg"), NOW)
        self.assertIn("분 전", sp.skip_reason(f, "123456", NOW))

    def test_same_picture_skips_even_if_old(self):
        f = sp.clock_files(info("sc_1125_1900_123456.jpg"), NOW)
        self.assertIn("같은 그림", sp.skip_reason(f, "123456", NOW))

    def test_old_different_picture_uploads(self):
        f = sp.clock_files(info("sc_1125_2120_abcdef.jpg"), NOW)
        self.assertIsNone(sp.skip_reason(f, "123456", NOW))

    def test_unreachable_clock_is_quiet(self):
        state = sp.STATE.read_text(encoding="utf-8") if sp.STATE.exists() else None
        try:
            img = sp.render_text("시험", ["한글"])
            sp.push("127.0.0.1:9", img, force=True)   # 닫힌 포트 → 예외 없이 한 줄만
        finally:
            if state is None and sp.STATE.exists():
                sp.STATE.unlink()


class Fonts(unittest.TestCase):
    def test_korean_font_found(self):
        f = sp.font(20)
        self.assertGreater(f.getlength("농막"), 10)


if __name__ == "__main__":
    unittest.main()
