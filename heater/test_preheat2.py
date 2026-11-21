#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""2노드 예열 모델 테스트(11-22 연구 값).  실행: python3 -m unittest -v test_preheat2"""
import json, os, copy, datetime, unittest
import heater_control as hc

CFG = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "heater_config.json"), encoding="utf-8"))
P2 = hc.PREHEAT2_DEFAULT


def cfg_with(model, **pre):
    c = copy.deepcopy(CFG); c["preheat"]["model"] = model; c["preheat"].update(pre); return c


class Lead2(unittest.TestCase):
    def test_reproduces_past_preheats(self):   # 실제: 11-20 10.7h, 11-21 아침 2.1h, 11-22 약 6.5h(25°C, 보조 켬)
        self.assertAlmostEqual(hc.lead2_hours(P2, 8.3, 8.0, 0.3, 1820, 0, 23, 14), 10.4, delta=0.5)
        self.assertAlmostEqual(hc.lead2_hours(P2, 19.7, 18.8, -1.5, 1780, 0, 23, 14), 3.6, delta=0.5)
        self.assertAlmostEqual(hc.lead2_hours(P2, 14.1, 15.8, -1.3, 1835, 477, 25, 14), 6.7, delta=0.5)

    def test_warm_furniture_is_faster(self):   # 같은 공기 온도라도 가구가 따뜻하면 빨리 닿는다
        cold = hc.lead2_hours(P2, 15, 10, 0, 1800, 0, 23, 14)
        warm = hc.lead2_hours(P2, 15, 20, 0, 1800, 0, 23, 14)
        self.assertLess(warm, cold)

    def test_aux_shortens(self):
        self.assertLess(hc.lead2_hours(P2, 10, 10, -2, 1800, 470, 23, 14), hc.lead2_hours(P2, 10, 10, -2, 1800, 0, 23, 14))

    def test_unreachable_none_and_already_warm(self):
        self.assertIsNone(hc.lead2_hours(P2, 5, 5, -10, 500, 0, 23, 12))
        self.assertEqual(hc.lead2_hours(P2, 23.5, 20, 0, 1800, 0, 23, 12), 0.0)


class MassStep(unittest.TestCase):
    def test_converges_to_balance(self):   # 1층 20, 바깥 0 → 가구 평형 175·20/(175+10) ≈ 18.9
        tm = 10.0
        for _ in range(600): tm = hc.mass_step(tm, 20.0, 0.0, 60, P2)
        self.assertAlmostEqual(tm, 18.9, delta=0.1)

    def test_start_and_missing(self):
        self.assertEqual(hc.mass_step(None, 12.0, 0, 60, P2), 12.0)
        self.assertEqual(hc.mass_step(15.0, None, 0, 60, P2), 15.0)
        self.assertEqual(hc.mass_step(15.0, 12.0, 0, 0, P2), 15.0)


class Selection(unittest.TestCase):
    def test_default_and_shadow_use_1node(self):
        for m in ("1node", "shadow"):
            c = cfg_with(m)
            self.assertEqual(hc.preheat_lead_hours(c, 12, 0, 1800, 15.0), hc.preheat_lead_hours1(c, 12, 0, 1800))

    def test_2node_margin_and_cap(self):
        c = cfg_with("2node", target_temp=23)
        raw = hc.lead2_hours(hc.p2_cfg(c), 19.7, 18.8, -1.5, 1780, 0, 23, 12)
        self.assertAlmostEqual(hc.preheat_lead_hours(c, 19.7, -1.5, 1780, 18.8), raw * P2["margin"], places=6)
        self.assertEqual(hc.preheat_lead_hours(c, 5, -10, 500, 5), c["preheat"]["max_lead_hours"])   # 못 닿음 → 상한
        self.assertEqual(hc.preheat_lead_hours(c, None, 0, 1800), c["preheat"]["max_lead_hours"])

    def test_two_node_overrides(self):
        c = cfg_with("2node", two_node={"margin": 1.0})
        self.assertEqual(hc.p2_cfg(c)["margin"], 1.0); self.assertEqual(hc.p2_cfg(c)["h_am"], P2["h_am"])

    def test_cold_morning_not_capped(self):   # 11-21 아침(−1.5°C): 1노드는 '도달 불가'로 상한, 2노드는 몇 시간
        c1 = cfg_with("1node", target_temp=23); c2 = cfg_with("2node", target_temp=23)
        self.assertEqual(hc.preheat_lead_hours(c1, 19.7, -2.6, 1780), c1["preheat"]["max_lead_hours"])
        self.assertLess(hc.preheat_lead_hours(c2, 19.7, -2.6, 1780, 18.8), 6)


class ArrivalShadow(unittest.TestCase):
    T0 = 1_800_000_000

    def test_shadow_records_both_and_history(self):
        c = cfg_with("shadow"); t0 = self.T0
        st = {"mode": "frost", "on": 3, "off": 5, "source": "default", "since": 0, "last_cmd_ts": 0,
              "arrival": {"ts": t0 + 6 * 3600, "target": 23}, "t_mass": 15.0}
        dtn = datetime.datetime.fromtimestamp(t0)
        ch, logs = hc.schedule_step(c, st, 14.0, 0.0, dtn, t0, 1800, 0.0, 0.0)
        self.assertEqual(st["mode"], "comfort")
        run = st["preheat_run"]; self.assertEqual(run["model"], "shadow"); self.assertIn("lead2_h", run)
        later = t0 + 5 * 3600
        ch, logs = hc.schedule_step(c, st, 23.1, 0.0, datetime.datetime.fromtimestamp(later), later)
        self.assertNotIn("preheat_run", st)
        h = st["preheat_hist"][-1]; self.assertAlmostEqual(h["actual_h"], 5.0); self.assertIsNotNone(h["lead2_h"])
        self.assertTrue(any("2노드" in m for m in logs))

    def test_2node_plan_uses_t_mass_and_predict_runs(self):
        c = cfg_with("2node"); t0 = self.T0
        st = {"mode": "frost", "arrival": {"ts": t0 + 20 * 3600, "target": 23}}
        cold = hc.arrival_plan(c, dict(st, t_mass=8.0), 12.0, 0.0, 1800)[1]
        warm = hc.arrival_plan(c, dict(st, t_mass=18.0), 12.0, 0.0, 1800)[1]
        self.assertLess(warm, cold)
        s, T, lead = hc.predict_arrival_start(c, dict(st, t_mass=18.0, frost=None), 18.0, t0, [(t0 + h * 3600, 0.0) for h in range(24)], 0.0, 1800)
        self.assertTrue(t0 <= s <= t0 + 20 * 3600)
        self.assertLess(T, 18.0)   # 시작 전까지 식는다


if __name__ == "__main__":
    unittest.main()
