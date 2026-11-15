#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fan_control.decide 단위 테스트. 실행: cd heater && python3 -m unittest test_fan_control"""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import fan_control as fc

C = dict(fc.DEFAULTS)
N = 1_800_000_000


def T(f, a):
    return {"floor": f, "attic": a}


class Decide(unittest.TestCase):
    def test_on_when_heating_and_stratified(self):
        on, why, _ = fc.decide(N, C, {"mode": "comfort", "relay": True}, T(24.0, 26.0), {}, {}, False)
        self.assertTrue(on)

    def test_band_hysteresis(self):
        s = {"mode": "comfort", "relay": True}
        self.assertFalse(fc.decide(N, C, s, T(24.0, 24.8), {}, {}, False)[0])   # 0.8 < 1.0 → 안 켬
        self.assertTrue(fc.decide(N, C, s, T(24.0, 25.0), {}, {}, False)[0])    # 1.0 → 켬
        self.assertTrue(fc.decide(N, C, s, T(24.0, 24.5), {}, {}, True)[0])     # 켜진 상태면 0.3 초과라 유지
        self.assertFalse(fc.decide(N, C, s, T(24.0, 24.3), {}, {}, True)[0])    # 0.3 이하 → 끔

    def test_runs_while_heater_off_by_default(self):
        self.assertTrue(fc.decide(N, C, {"mode": "comfort", "relay": False}, T(24, 25.5), {}, {}, False)[0])

    def test_require_heater_option(self):
        c = dict(C, require_heater=True)
        _, _, st = fc.decide(N, c, {"mode": "comfort", "relay": True}, T(24, 26), {}, {}, False)
        self.assertTrue(fc.decide(N + 240, c, {"mode": "comfort", "relay": False}, T(24, 26), {}, st, True)[0])
        self.assertFalse(fc.decide(N + 400, c, {"mode": "comfort", "relay": False}, T(24, 26), {}, st, True)[0])

    def test_sleep_and_frost_off(self):
        for m in ("sleep", "frost"):
            on, why, _ = fc.decide(N, C, {"mode": m, "relay": True}, T(20, 24), {}, {}, True)
            self.assertFalse(on); self.assertIn(m, why)

    def test_stale_sensor_off(self):
        self.assertFalse(fc.decide(N, C, {"mode": "comfort", "relay": True}, T(None, 26), {}, {}, True)[0])

    def test_manual_overrides_then_expires(self):
        s = {"mode": "frost", "relay": False}
        self.assertTrue(fc.decide(N, C, s, T(4, 4), {"mode": "on", "ts": N - 60}, {}, False)[0])
        self.assertFalse(fc.decide(N, C, s, T(4, 4), {"mode": "on", "ts": N - 5 * 3600}, {}, True)[0])
        s2 = {"mode": "comfort", "relay": True}
        self.assertFalse(fc.decide(N, C, s2, T(20, 25), {"mode": "off", "ts": N - 60}, {}, True)[0])
        self.assertTrue(fc.decide(N, C, s2, T(20, 25), {"mode": "auto", "ts": N - 60}, {}, False)[0])

    def test_hot_with_presence_any_mode(self):
        for m in ("frost", "sleep", "comfort"):
            on, why, st = fc.decide(N, C, {"mode": m, "relay": False}, T(28.5, 29.0), {}, {}, False, present=True)
            self.assertTrue(on, m); self.assertIn("더위", why)

    def test_hot_band_and_presence(self):
        s = {"mode": "frost", "relay": False}
        self.assertFalse(fc.decide(N, C, s, T(28.5, 29), {}, {}, False, present=False)[0])   # 무인 → 안 켬
        self.assertFalse(fc.decide(N, C, s, T(27.5, 28), {}, {}, False, present=True)[0])    # 28 미만 → 안 켬
        _, _, st = fc.decide(N, C, s, T(28.0, 28), {}, {}, False, present=True)
        self.assertTrue(fc.decide(N, C, s, T(27.5, 28), {}, st, True, present=True)[0])      # 켜진 뒤 27 초과면 유지
        self.assertFalse(fc.decide(N, C, s, T(27.0, 28), {}, st, True, present=True)[0])     # 27 이하 → 끔

    def test_hot_not_while_heating_and_manual_wins(self):
        self.assertFalse(fc.decide(N, C, {"mode": "sleep", "relay": True}, T(28.5, 28.6), {}, {}, False, present=True)[0])
        self.assertFalse(fc.decide(N, C, {"mode": "frost", "relay": False}, T(30, 30), {"mode": "off", "ts": N}, {}, True,
                                   present=True)[0])

    def test_summer_ac_mixing_any_mode(self):
        t = {"floor": 24.0, "attic": 27.0, "out": 30.0}       # 에어컨으로 1층 24, 다락 27, 바깥 30
        on, why, _ = fc.decide(N, C, {"mode": "sleep", "relay": False}, t, {}, {}, False, present=True)
        self.assertTrue(on); self.assertIn("여름", why)
        self.assertFalse(fc.decide(N, C, {"mode": "sleep", "relay": False}, t, {}, {}, False, present=False)[0])
        cold = dict(t, out=7.0)                                # 겨울 밤 취침은 그대로 끔
        self.assertFalse(fc.decide(N, C, {"mode": "sleep", "relay": False}, cold, {}, {}, False, present=True)[0])

    def test_temps_from_staleness(self):
        lat = {"내부1층": {"temp": 24.0, "time_ms": (N - 60) * 1000}, "다락": {"temp": 26.0, "time_ms": (N - 3600) * 1000}}
        self.assertEqual(fc.temps_from(lat, N, 10), {"floor": 24.0, "attic": None, "out": None})


if __name__ == "__main__":
    unittest.main()
