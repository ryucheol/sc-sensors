#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""heater_cmd 온도 인자 해석 테스트 — '난방 18도' 같은 음성·자연어 입력.  실행: cd heater && python3 -m unittest -v test_heater_cmd"""
import os, sys, json, datetime, shutil, tempfile, subprocess, unittest
HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import heater_cmd as hc   # noqa: E402


class ParseTemp(unittest.TestCase):
    def test_forms(self):
        for s, v in (("18", 18.0), ("18도", 18.0), ("18.5℃", 18.5), ("18°C", 18.0), ("18°", 18.0), ("18c", 18.0), ("-3도", -3.0)):
            self.assertEqual(hc.parse_temp(s), v, s)

    def test_not_temp(self):
        for s in ("내일", "16:00", "18도요", "열여덟도", "2026-11-14"):
            self.assertIsNone(hc.parse_temp(s), s)


class Cli(unittest.TestCase):
    """실제 명령 실행: 임시 HOME 의 ~/smart-cabin 에 설정을 두고 state 결과 확인."""
    def setUp(self):
        self.home = tempfile.mkdtemp(); self.base = os.path.join(self.home, "smart-cabin"); os.makedirs(self.base)
        shutil.copy(os.path.join(os.path.dirname(HERE), "heater_config.json"), self.base)

    def tearDown(self):
        shutil.rmtree(self.home)

    def run_cmd(self, *args):
        return subprocess.run([sys.executable, os.path.join(HERE, "heater_cmd.py")] + list(args), capture_output=True,
                              text=True, env=dict(os.environ, HOME=self.home, PYTHONIOENCODING="utf-8"))

    def state(self):
        return json.load(open(os.path.join(self.base, "heater_state.json")))

    def test_heat_18do(self):
        r = self.run_cmd("난방", "18도"); self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        st = self.state(); self.assertEqual(st["mode"], "comfort"); self.assertEqual(st["on"], 18.0)

    def test_unreadable_number_is_error_not_default(self):
        r = self.run_cmd("난방", "18도요")
        self.assertNotEqual(r.returncode, 0); self.assertIn("온도를 읽지 못했습니다", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.base, "heater_state.json")))   # 기본 23도로 바뀌지 않음


class ArrivalKeep(unittest.TestCase):
    def test_used_up(self):
        import heater_cmd as hcmd
        now = 1_800_000_000
        self.assertTrue(hcmd.arrival_used_up(None, now))
        self.assertTrue(hcmd.arrival_used_up({"ts": now - 3600}, now))          # 지남
        self.assertTrue(hcmd.arrival_used_up({"ts": now + 2 * 3600}, now))      # 곧 도착(현장 도착 무렵)
        self.assertFalse(hcmd.arrival_used_up({"ts": now + 5 * 86400}, now))    # 며칠 뒤(캘린더) → 유지



class ParseWhen(unittest.TestCase):
    NOW = datetime.datetime(2026, 11, 16, 13, 58)
    def w(self, *a): return hc.parse_when(list(a), now=self.NOW)
    def test_month_day(self):   # 11-16 사고: '도착 11-20 19:00 23.5'가 오늘 19:00으로 잡힘
        self.assertEqual(self.w("11-20", "19:00", "23.5"), datetime.datetime(2026, 11, 20, 19, 0))
        self.assertEqual(self.w("11/20", "19:00"), datetime.datetime(2026, 11, 20, 19, 0))
        self.assertEqual(self.w("11월", "20일", "19:00"), datetime.datetime(2026, 11, 20, 19, 0))
        self.assertEqual(self.w("11월20일", "19:00"), datetime.datetime(2026, 11, 20, 19, 0))
    def test_iso_and_relative(self):
        self.assertEqual(self.w("2026-11-20", "19:00"), datetime.datetime(2026, 11, 20, 19, 0))
        self.assertEqual(self.w("내일", "07:00"), datetime.datetime(2026, 11, 17, 7, 0))
        self.assertEqual(self.w("19:00"), datetime.datetime(2026, 11, 16, 19, 0))
        self.assertEqual(self.w("10:00"), datetime.datetime(2026, 11, 17, 10, 0))   # 지난 시각 → 내일
    def test_next_year(self):
        self.assertEqual(self.w("1-3", "10:00"), datetime.datetime(2027, 1, 3, 10, 0))
    def test_unknown_word_rejected(self):
        with self.assertRaises(ValueError): self.w("금요일", "19:00")
    def test_past_explicit_rejected(self):
        with self.assertRaises(ValueError): self.w("2026-11-16", "09:00")

if __name__ == "__main__":
    unittest.main()
