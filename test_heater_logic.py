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
        self.assertTrue(changed); self.assertEqual((s["mode"], s["source"], s["on"], s["off"]), ("comfort", "preheat", 23, 23.5))
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

R = hc.red_cfg(CFG)

class SensorOk(unittest.TestCase):
    def test_fresh_ok(self):        self.assertTrue(hc.sensor_ok(5.0, 1000, 1000 + 60_000, 30 * 60_000))
    def test_stale(self):           self.assertFalse(hc.sensor_ok(5.0, 0, 31 * 60_000, 30 * 60_000))
    def test_missing(self):
        self.assertFalse(hc.sensor_ok(None, 0, 0, 1)); self.assertFalse(hc.sensor_ok(5.0, None, 0, 1))
    def test_out_of_range(self):    # 배터리 방전 등 쓰레기값
        self.assertFalse(hc.sensor_ok(-40.0, 0, 0, 1)); self.assertFalse(hc.sensor_ok(85.0, 0, 0, 1))

class Delta(unittest.TestCase):
    def test_moves_toward_observed(self):
        d = hc.update_delta(3.0, 24.0, 22.0, 600, 3600)          # 관측 차 2.0, 10분/60분
        self.assertTrue(2.0 < d < 3.0); self.assertAlmostEqual(d, 3.0 + (1/6) * (2.0 - 3.0))
    def test_zero_dt_noop(self):    self.assertEqual(hc.update_delta(3.0, 30, 0, 0, 3600), 3.0)
    def test_clamped(self):         self.assertEqual(hc.update_delta(0.0, 50, 0, 10**6, 3600), 10.0)

class Fuse(unittest.TestCase):
    # 1층 제어(동파). 다락−1층 delta=3 → d(1층−다락) = −3
    def test_both_ok_uses_primary(self):
        self.assertEqual(hc.fuse_control("frost", 4.0, True, 7.2, True, -3.0, R), (4.0, "primary"))
    def test_primary_only(self):
        self.assertEqual(hc.fuse_control("frost", 4.0, True, None, False, -3.0, R), (4.0, "primary-only"))
    def test_fallback_frost_is_conservative(self):   # 1층 고장 → 다락 7 − 3 − 여유 1.5 = 2.5 → 켜짐 쪽
        t, src = hc.fuse_control("frost", None, False, 7.0, True, -3.0, R)
        self.assertEqual(src, "fallback"); self.assertAlmostEqual(t, 7.0 - 3.0 - R["fallback_margin_frost"])
        self.assertTrue(hc.decide_relay("frost", 3, 5, t, False, False))
    def test_fallback_comfort_no_margin(self):
        t, src = hc.fuse_control("comfort", None, False, 25.0, True, -2.0, R)
        self.assertEqual((t, src), (23.0, "fallback"))
    def test_sleep_attic_dead_uses_floor(self):       # 취침: 다락 고장 → 1층 21 + delta 2 = 23
        self.assertEqual(hc.fuse_control("sleep", None, False, 21.0, True, 2.0, R), (23.0, "fallback"))
    def test_disagree_frost_takes_colder(self):       # 1층이 20°C로 튀어도(고장) 다락 기준 추정 4−3−1.5 쪽을 믿음
        t, src = hc.fuse_control("frost", 20.0, True, 4.0, True, -3.0, R)
        self.assertEqual(src, "disagree-min"); self.assertAlmostEqual(t, 4.0 - 3.0 - R["fallback_margin_frost"])
    def test_disagree_comfort_keeps_primary(self):
        self.assertEqual(hc.fuse_control("comfort", 10.0, True, 25.0, True, -2.0, R), (10.0, "disagree"))
    def test_both_dead(self):
        self.assertEqual(hc.fuse_control("frost", None, False, None, False, -3.0, R), (None, "none"))

class Pair(unittest.TestCase):
    def test_pair(self): self.assertEqual(hc.indoor_pair(CFG), ("내부1층", "다락"))
    def test_no_attic(self):
        cfg = dict(CFG); cfg["sleep"] = {"on": 20, "off": 21}
        self.assertEqual(hc.indoor_pair(cfg), ("내부1층", None))

DM = hc.dm_cfg(CFG)

class Deadman(unittest.TestCase):
    def test_off_first_time_arms(self):   self.assertEqual(hc.deadman_action(False, None, None, DM), ("arm", 1800))
    def test_off_recent_no_write(self):    self.assertIsNone(hc.deadman_action(False, 1700, 60, DM))
    def test_off_rearm_after_interval(self): self.assertEqual(hc.deadman_action(False, 1500, 300, DM), ("arm", 1800))
    def test_off_cleared_externally_rearms(self):   # 앱에서 지웠거나 플러그 재부팅
        self.assertEqual(hc.deadman_action(False, 0, 60, DM), ("arm", 1800))
    def test_on_clears_pending(self):      self.assertEqual(hc.deadman_action(True, 1700, 60, DM), ("clear", 0))
    def test_on_clears_when_armed_unreported(self): self.assertEqual(hc.deadman_action(True, None, 60, DM), ("clear", 0))
    def test_on_nothing_pending(self):     self.assertIsNone(hc.deadman_action(True, 0, None, DM))
    def test_unknown_relay_noop(self):     self.assertIsNone(hc.deadman_action(None, 0, None, DM))
    def test_disabled(self):
        self.assertIsNone(hc.deadman_action(False, None, None, dict(DM, enabled=False)))
    def test_fires_before_freeze(self):    # 30분: 외기 −19°C, 실내 3°C에서 냉각 ~2.4°C/h → 켜질 때 ~1.8°C
        self.assertLessEqual(DM["sec"], 1800)


class TestStage(unittest.TestCase):
    def test_detect(self):
        self.assertEqual(hc.detect_stage(720), 1); self.assertEqual(hc.detect_stage(1086), 2)
        self.assertEqual(hc.detect_stage(1775), 3); self.assertIsNone(hc.detect_stage(0)); self.assertIsNone(hc.detect_stage(2500))
    def test_preheat_uses_measured_P(self):
        # 1단(720W)이면 외기 0°C에서 T_eq=15°C → 목표 도달 불가 → 최대 선행
        self.assertEqual(hc.preheat_lead_hours(CFG, 5, 0, 720), CFG["preheat"]["max_lead_hours"])
        self.assertLess(hc.preheat_lead_hours(CFG, 5, 0, 1763), CFG["preheat"]["max_lead_hours"])
    def test_advice_frost(self):
        cfg = dict(CFG); cfg["stages"] = [720, 1102, 1763]
        a = hc.stage_advice(cfg, "frost", 1, 720, -12, None, None, None, False, 0, False)   # T_eq=3.3<5
        self.assertIn("동파 위험", a)
        a = hc.stage_advice(cfg, "frost", 1, 720, 0, None, None, None, False, 0, False)
        self.assertIn("예열 불가", a)
        self.assertIsNone(hc.stage_advice(cfg, "frost", 3, 1763, 0, None, None, None, False, 0, False))
    def test_advice_occupied(self):
        cfg = dict(CFG); cfg["stages"] = [720, 1102, 1763]
        a = hc.stage_advice(cfg, "comfort", 1, 720, None, 10, 22.4, 23.0, True, 75, True)
        self.assertIn("출력 부족", a)
        self.assertIsNone(hc.stage_advice(cfg, "comfort", 1, 720, None, 10, 23.2, 23.0, False, 0, False))
