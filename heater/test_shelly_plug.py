#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shelly 드라이버 테스트(기기·네트워크 불필요).  실행: cd heater && python3 -m unittest -v test_shelly_plug"""
import os, sys, hashlib, unittest
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import shelly_plug as sp   # noqa: E402
import heater_control as hc   # noqa: E402

ST_ON = {"switch:0": {"id": 0, "output": True, "apower": 1763.4, "voltage": 221.7, "current": 7.95,
                      "aenergy": {"total": 12345.6}, "temperature": {"tC": 41.2}},
         "sys": {"unixtime": 1794470000}}
ST_OFF_TIMER = {"switch:0": {"id": 0, "output": False, "apower": 0.0, "voltage": 222.0, "aenergy": {"total": 12000},
                             "timer_started_at": 1794469700.0, "timer_duration": 1800, "timer_type": "toggle_after"},
                "sys": {"unixtime": 1794470000}}


class FakeRPC:
    def __init__(self, status):
        self.status, self.calls = status, []

    def __call__(self, method, params=None):
        self.calls.append((method, params))
        if method == "Shelly.GetStatus":
            return self.status
        if method == "Switch.GetStatus":
            return self.status["switch:0"]
        return {"was_on": self.status["switch:0"]["output"]}


class Dps(unittest.TestCase):
    def test_units_match_tuya(self):
        d = sp.to_dps(ST_ON)
        self.assertIs(d["1"], True)
        self.assertEqual(d["19"], 17634); self.assertEqual(d["20"], 2217); self.assertEqual(d["17"], 1235)
        self.assertEqual(d["9"], 0); self.assertEqual(d["temp_c"], 41.2)
        # heater_control 가 쓰는 환산식 그대로 원래 값이 나오는가
        self.assertAlmostEqual(d["19"] / 10.0, 1763.4); self.assertAlmostEqual(d["17"] / 100.0, 12.35)

    def test_countdown_remaining(self):
        self.assertEqual(sp.to_dps(ST_OFF_TIMER)["9"], 1500)
        no_clock = {"switch:0": ST_OFF_TIMER["switch:0"], "sys": {"unixtime": None}}
        self.assertIsNone(sp.to_dps(no_clock)["9"])          # 시각 모름 → 보고 없음

    def test_deadman_logic_compatible(self):
        dm = dict(hc.DM_DEFAULT)
        self.assertIsNone(hc.deadman_action(False, sp.to_dps(ST_OFF_TIMER)["9"], 60, dm))   # 1500초 남음·방금 연장 → 그대로
        self.assertEqual(hc.deadman_action(True, sp.to_dps(ST_ON)["9"], 60, dm), ("clear", 0))


class Commands(unittest.TestCase):
    def test_on_off(self):
        f = FakeRPC(ST_ON); p = sp.ShellyPlug("x", transport=f)
        p.turn_on(); p.turn_off()
        self.assertEqual(f.calls, [("Switch.Set", {"id": 0, "on": True}), ("Switch.Set", {"id": 0, "on": False})])

    def test_arm_keeps_state(self):
        f = FakeRPC(ST_OFF_TIMER); p = sp.ShellyPlug("x", transport=f)
        p.set_value(9, 1800)
        self.assertEqual(f.calls[-1], ("Switch.Set", {"id": 0, "on": False, "toggle_after": 1800}))

    def test_clear_without_timer(self):
        f = FakeRPC(ST_ON); p = sp.ShellyPlug("x", transport=f)
        p.set_value(9, 0)
        self.assertEqual(f.calls[-1], ("Switch.Set", {"id": 0, "on": True}))

    def test_other_dp_rejected(self):
        with self.assertRaises(ValueError):
            sp.ShellyPlug("x", transport=FakeRPC(ST_ON)).set_value(1, True)

    def test_make_device(self):
        self.assertIsInstance(sp.make_device({"type": "shelly", "ip": "1.2.3.4"}), sp.ShellyPlug)


class Auth(unittest.TestCase):
    def test_parse(self):
        c = sp.parse_www_auth('Digest qop="auth", realm="shellyplugmg3-abc", nonce="60dc59c6", algorithm=SHA-256')
        self.assertEqual(c, {"qop": "auth", "realm": "shellyplugmg3-abc", "nonce": "60dc59c6", "algorithm": "SHA-256"})

    def test_digest_response(self):
        c = {"realm": "r", "nonce": "n", "qop": "auth"}
        h = sp.digest_header(c, "pw", cnonce="c", nc="00000001")
        H = lambda s: hashlib.sha256(s.encode()).hexdigest()
        exp = H("%s:n:00000001:c:auth:%s" % (H("admin:r:pw"), H("POST:/rpc")))
        self.assertIn('response="%s"' % exp, h); self.assertIn("algorithm=SHA-256", h)


if __name__ == "__main__":
    unittest.main()
