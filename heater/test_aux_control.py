#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""aux_control.decide 단위 테스트. 실행: cd heater && python3 -m unittest test_aux_control"""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import aux_control as ac

C = dict(ac.DEFAULTS)
N = 1_800_000_000
FROST = {"mode": "frost", "on": 3, "off": 5, "source": "cmd"}
COMFORT = {"mode": "comfort", "on": 25, "off": 25.5, "source": "cmd"}
ARRIVAL = {"mode": "comfort", "on": 23.5, "off": 24, "source": "arrival"}
OK = {"relay": True, "heater_ok": True, "status_fail_streak": 0}


class Decide(unittest.TestCase):
    def test_frost_danger_band(self):
        self.assertTrue(ac.decide(N, C, FROST, OK, 2.5, {}, False)[0])
        self.assertFalse(ac.decide(N, C, FROST, OK, 2.8, {}, False)[0])
        self.assertTrue(ac.decide(N, C, FROST, OK, 3.5, {}, True)[0])       # 켜진 뒤 4.0까지 유지
        self.assertFalse(ac.decide(N, C, FROST, OK, 4.0, {}, True)[0])

    def test_frost_danger_beats_manual_off(self):
        self.assertTrue(ac.decide(N, C, COMFORT, OK, 2.0, {"mode": "off", "ts": N - 60}, False)[0])

    def test_attended_off_and_manual(self):
        self.assertFalse(ac.decide(N, C, COMFORT, OK, 18.0, {}, False)[0])      # 재실 난방: 시끄러우니 자동으론 안 켬
        self.assertFalse(ac.decide(N, C, {"mode": "sleep", "on": 22, "source": "cmd"}, OK, 17.0, {}, False)[0])
        self.assertTrue(ac.decide(N, C, COMFORT, OK, 18.0, {"mode": "on", "ts": N - 60}, False)[0])
        self.assertFalse(ac.decide(N, C, COMFORT, OK, 18.0, {"mode": "on", "ts": N - 3 * 3600}, True)[0])   # 2시간 지나면 자동

    def test_arrival_boost_band(self):
        self.assertTrue(ac.decide(N, C, ARRIVAL, OK, 20.0, {}, False)[0])
        self.assertFalse(ac.decide(N, C, ARRIVAL, OK, 22.8, {}, False)[0])     # 목표−1 이상이면 안 켬
        self.assertTrue(ac.decide(N, C, ARRIVAL, OK, 22.8, {}, True)[0])       # 켜진 뒤 목표−0.3까지
        self.assertFalse(ac.decide(N, C, ARRIVAL, OK, 23.3, {}, True)[0])

    def test_main_fault_backup_in_frost(self):
        bad = {"relay": True, "heater_ok": False}
        self.assertTrue(ac.decide(N, C, FROST, bad, 4.5, {}, False)[0])
        self.assertTrue(ac.decide(N, C, FROST, {"status_fail_streak": 5}, 4.5, {}, False)[0])
        self.assertFalse(ac.decide(N, C, FROST, bad, 8.0, {}, False)[0])       # 아직 따뜻하면 안 켬
        self.assertFalse(ac.decide(N, C, FROST, OK, 4.5, {}, False)[0])        # 메인 정상 → 평시

    def test_stale_sensor(self):
        self.assertFalse(ac.decide(N, C, FROST, OK, None, {}, False)[0])
        self.assertIsNone(ac.floor_temp({"내부1층": {"temp": 2.0, "time_ms": (N - 3600) * 1000}}, N, 15))


class SelfTest(unittest.TestCase):
    def st(self, h_ahead, mode="frost"):
        return {"mode": mode, "arrival": {"ts": N + h_ahead * 3600, "target": 23.5}}

    def test_due_within_window_once(self):
        self.assertTrue(ac.selftest_due(N, C, self.st(8), {}, False, {}))
        self.assertFalse(ac.selftest_due(N, C, self.st(8), {}, False, {"selftest_for": N + 8 * 3600}))   # 이 예약은 이미

    def test_not_due(self):
        self.assertFalse(ac.selftest_due(N, C, self.st(30), {}, False, {}))            # 아직 멂
        self.assertFalse(ac.selftest_due(N, C, self.st(-1), {}, False, {}))            # 지난 예약
        self.assertFalse(ac.selftest_due(N, C, self.st(8, "comfort"), {}, False, {}))  # 이미 예열 중
        self.assertFalse(ac.selftest_due(N, C, self.st(8), {}, True, {}))              # 사람이 있음
        self.assertFalse(ac.selftest_due(N, C, self.st(8), {"mode": "off", "ts": N - 60}, False, {}))   # 수동 끄기
        self.assertFalse(ac.selftest_due(N, C, {"mode": "frost"}, {}, False, {}))      # 예약 없음

    def test_observe(self):
        m = ac.observe(N, C, True, 470, {})
        self.assertTrue(m["heat_ok"])
        m = ac.observe(N, C, True, 0, m)                      # 막 0 W: 아직 판정 안 함
        self.assertTrue(m["heat_ok"])
        m = ac.observe(N + C["bad_after_s"], C, True, 0, m)   # 2분 이어짐 → 안 됨
        self.assertFalse(m["heat_ok"])
        m = ac.observe(N + 999, C, False, 0, m)               # 꺼져 있으면 판정 유지
        self.assertFalse(m["heat_ok"])


if __name__ == "__main__":
    unittest.main()
