#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""heater_control 순수 판단 로직 테스트 (tinytuya·하드웨어 불필요).  실행: python3 -m unittest -v test_heater_logic"""
import json, datetime, unittest
import heater_control as hc

CFG = json.load(open("heater_config.json", encoding="utf-8"))
FRI = datetime.datetime(2026, 11, 6)   # 금요일(weekday 4)
assert FRI.weekday() == 4

def st(mode="frost", source="default", last_cmd_ts=0, **kw):
    c = CFG[mode]
    d = {"mode": mode, "on": c["on"], "off": c["off"], "source": source, "since": 0, "last_cmd_ts": last_cmd_ts, "relay": False}
    d.update(kw); return d

def at(h, m=0, base=FRI): return base.replace(hour=h, minute=m)

class Hysteresis(unittest.TestCase):
    def test_on_below(self):   self.assertTrue(hc.decide_relay("frost", 3, 5, 2.9, False, False))
    def test_off_above(self):  self.assertFalse(hc.decide_relay("frost", 3, 5, 5.1, False, True))
    def test_band_holds_on(self):  self.assertTrue(hc.decide_relay("frost", 3, 5, 4.0, False, True))
    def test_band_holds_off(self): self.assertFalse(hc.decide_relay("frost", 3, 5, 4.0, False, False))
    def test_boundaries_hold(self):   # 경계값(==on, ==off)은 현상유지
        self.assertTrue(hc.decide_relay("frost", 3, 5, 3.0, False, True))
        self.assertFalse(hc.decide_relay("frost", 3, 5, 5.0, False, False))
    def test_stale_frost_forces_on(self):
        self.assertTrue(hc.decide_relay("frost", 3, 5, None, True, False))
    def test_stale_comfort_holds(self):
        self.assertTrue(hc.decide_relay("comfort", 23, 24, None, True, True))
        self.assertFalse(hc.decide_relay("comfort", 23, 24, None, True, False))
    def test_stale_sleep_holds(self):
        self.assertFalse(hc.decide_relay("sleep", 20, 21, None, True, False))

class ControlSensor(unittest.TestCase):
    def test_sleep_uses_attic(self):  self.assertEqual(hc.control_sensor(CFG, "sleep"), "다락")
    def test_others_use_main(self):
        for m in ("frost", "comfort"): self.assertEqual(hc.control_sensor(CFG, m), "내부1층")
    def test_sleep_without_sensor_falls_back(self):
        cfg = dict(CFG); cfg["sleep"] = {"on": 20, "off": 21}
        self.assertEqual(hc.control_sensor(cfg, "sleep"), "내부1층")

class PreheatLead(unittest.TestCase):
    def test_already_warm(self):     self.assertEqual(hc.preheat_lead_hours(CFG, 23.5, 0), 0.0)
    def test_unknown_inside_max(self): self.assertEqual(hc.preheat_lead_hours(CFG, None, 0), CFG["preheat"]["max_lead_hours"])
    def test_cold_outdoor_capped(self):
        self.assertEqual(hc.preheat_lead_hours(CFG, 3, -40), CFG["preheat"]["max_lead_hours"])
    def test_monotonic_colder_outside_longer(self):
        self.assertGreater(hc.preheat_lead_hours(CFG, 5, -5), hc.preheat_lead_hours(CFG, 5, 5))
    def test_within_bounds(self):
        v = hc.preheat_lead_hours(CFG, 5, 0)
        self.assertTrue(0 < v <= CFG["preheat"]["max_lead_hours"])

class Schedule(unittest.TestCase):
    def test_preheat_starts_when_lead_reached(self):
        s = st(); lead = hc.preheat_lead_hours(CFG, 5, 0)
        now = at(20) - datetime.timedelta(hours=lead) + datetime.timedelta(minutes=1)
        changed, logs = hc.schedule_step(CFG, s, 5, 0, now, now.timestamp())
        self.assertTrue(changed); self.assertEqual((s["mode"], s["source"], s["on"], s["off"]), ("comfort", "preheat", 23, 24))
        self.assertEqual(len(logs), 1)
    def test_preheat_not_before_lead(self):
        s = st(); lead = hc.preheat_lead_hours(CFG, 5, 0)
        now = at(20) - datetime.timedelta(hours=lead) - datetime.timedelta(minutes=5)
        changed, _ = hc.schedule_step(CFG, s, 5, 0, now, now.timestamp())
        self.assertFalse(changed); self.assertEqual(s["mode"], "frost")
    def test_no_preheat_other_weekday(self):
        s = st(); mon = datetime.datetime(2026, 11, 2, 19)
        self.assertFalse(hc.schedule_step(CFG, s, 5, 0, mon, 1000.0)[0])
    def test_preheat_disabled(self):
        cfg = json.loads(json.dumps(CFG)); cfg["preheat"]["enabled"] = False
        s = st(); self.assertFalse(hc.schedule_step(cfg, s, 5, 0, at(19, 59), 1000.0)[0])
    def test_no_preheat_after_target(self):
        s = st(); self.assertFalse(hc.schedule_step(CFG, s, 5, 0, at(21), 1000.0)[0])
    def test_no_preheat_when_already_comfort(self):
        s = st("comfort", "cmd", last_cmd_ts=900.0)
        self.assertFalse(hc.schedule_step(CFG, s, 5, 0, at(19), 1000.0)[0])
    def test_noshow_keeps_preheat_before_midnight(self):
        s = st("comfort", "preheat", since=FRI.replace(hour=17).timestamp())
        self.assertFalse(hc.schedule_step(CFG, s, 20, 0, FRI.replace(hour=23, minute=59), 1000.0)[0])
        self.assertEqual(s["mode"], "comfort")
    def test_noshow_reverts_preheat_after_midnight(self):   # 토요일 00:00 이후(금요일 블록 밖)에도 복귀해야 함
        s = st("comfort", "preheat", since=FRI.replace(hour=17).timestamp())
        for when in (datetime.datetime(2026, 11, 7, 0, 0), datetime.datetime(2026, 11, 7, 9), datetime.datetime(2026, 11, 9, 12)):
            s2 = dict(s); changed, _ = hc.schedule_step(CFG, s2, 20, 0, when, 1000.0)
            self.assertTrue(changed, when); self.assertEqual((s2["mode"], s2["source"], s2["on"], s2["off"]), ("frost", "noshow", 3, 5))
    def test_cmd_comfort_not_reverted_by_noshow(self):
        s = st("comfort", "cmd", last_cmd_ts=999.0)
        self.assertFalse(hc.schedule_step(CFG, s, 23, 0, at(23, 59), 1000.0)[0])
    def test_backstop_comfort(self):
        s = st("comfort", "cmd", last_cmd_ts=1000.0); h = CFG["backstop_comfort_hours"] * 3600
        changed, _ = hc.schedule_step(CFG, s, 23, 0, datetime.datetime(2026, 11, 3, 12), 1000.0 + h + 1)
        self.assertTrue(changed); self.assertEqual((s["mode"], s["source"], s["on"], s["off"]), ("frost", "backstop", 3, 5))
    def test_backstop_sleep_returns_to_main_sensor(self):
        s = st("sleep", "cmd", last_cmd_ts=1000.0); h = CFG["backstop_comfort_hours"] * 3600
        hc.schedule_step(CFG, s, 22, 0, datetime.datetime(2026, 11, 3, 12), 1000.0 + h + 1)
        self.assertEqual(s["mode"], "frost"); self.assertEqual(hc.control_sensor(CFG, s["mode"]), "내부1층")
    def test_backstop_not_yet(self):
        s = st("comfort", "cmd", last_cmd_ts=1000.0); h = CFG["backstop_comfort_hours"] * 3600
        self.assertFalse(hc.schedule_step(CFG, s, 23, 0, datetime.datetime(2026, 11, 3, 12), 1000.0 + h - 1)[0])
    def test_backstop_ignored_without_cmd_timestamp(self):
        s = st("comfort", "cmd", last_cmd_ts=0)
        self.assertFalse(hc.schedule_step(CFG, s, 23, 0, datetime.datetime(2026, 11, 3, 12), 9e9)[0])
    def test_frost_default_never_backstopped(self):
        s = st("frost", "default"); self.assertFalse(hc.schedule_step(CFG, s, 4, 0, datetime.datetime(2026, 11, 3, 12), 9e9)[0])

if __name__ == "__main__":
    unittest.main(verbosity=2)
