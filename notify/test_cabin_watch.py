#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cabin_watch 판단·전이 테스트 (인터넷·토큰·Pi 불필요). 실행: cd notify && python3 -m unittest -v test_cabin_watch"""
import os, sys, time, datetime, unittest

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import cabin_watch as cw   # noqa: E402

NOW = datetime.datetime(2026, 11, 12, 3, 0).timestamp()
CFG = {"sensor": "내부1층", "frost": {"on": 3, "off": 5}, "stale_temp_min": 45,
       "preheat": {"ua_w_per_k": 47}}


def stamp(ts): return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def status(**kw):
    s = {"updated": stamp(NOW - 60), "mode": "frost", "relay": False, "watt": 0, "t_ctl": 4.0,
         "heater_ok": True, "status_fail_streak": 0, "stage": 2, "stage_w": 1102}
    s.update(kw); return s


def latest(floor=4.0, attic=6.0, age=60):
    ms = (NOW - age) * 1000
    return {"내부1층": {"temp": floor, "time_ms": ms}, "다락": {"temp": attic, "time_ms": ms}}


def fc(tmin, at_h=20):
    rows = [{"ms": int((NOW + h * 3600) * 1000), "tmp": 2.0} for h in range(0, 48)]
    rows[at_h]["tmp"] = tmin
    return {"hourly": rows}


def ev(st=None, lt=None, f=None, cfg=CFG, mtime=None):
    return cw.evaluate(NOW, cfg, {}, st if st is not None else status(), lt if lt is not None else latest(),
                       f, mtime if mtime is not None else NOW)


def on(d): return sorted(k for k, v in d.items() if v[0])


class Evaluate(unittest.TestCase):
    def test_all_quiet(self):
        self.assertEqual(on(ev(f=fc(-2))), [])

    def test_overheat_any_mode(self):
        d = ev(st=status(mode="frost", relay=True, watt=1102), lt=latest(floor=29.0))
        self.assertIn("overheat", on(d))
        self.assertIn("내부1층 29.0°C", d["overheat"][1])
        d = ev(st=status(mode="comfort"), lt=latest(floor=24, attic=33))
        self.assertIn("다락 33.0°C", d["overheat"][1])

    def test_frost_stuck(self):
        d = ev(st=status(t_ctl=9.5, relay=True, watt=1102), lt=latest(floor=9.5))
        self.assertIn("frost_stuck", on(d))
        self.assertNotIn("frost_stuck", on(ev(st=status(t_ctl=9.5, relay=False, watt=0))))   # 햇볕 등 자연 상승
        self.assertNotIn("frost_stuck", ev(st=status(mode="comfort", t_ctl=23, relay=True, watt=1102)))

    def test_heater_fault_and_plug(self):
        self.assertIn("heater_fault", on(ev(st=status(heater_ok=False, relay=True, watt=0))))
        self.assertIn("plug_comm", on(ev(st=status(status_fail_streak=5))))
        self.assertNotIn("plug_comm", on(ev(st=status(status_fail_streak=2))))

    def test_controller_down(self):
        d = ev(st=status(updated=stamp(NOW - 30 * 60), heater_ok=False))
        self.assertIn("controller_down", on(d))
        self.assertNotIn("heater_fault", d)          # 낡은 상태로는 판단하지 않음(상태 유지)
        self.assertIn("controller_down", on(cw.evaluate(NOW, CFG, {}, None, latest(), None)))

    def test_low_indoor(self):
        d = ev(lt=latest(floor=1.8))
        self.assertIn("low_indoor", on(d)); self.assertIn("1.8°C", d["low_indoor"][1])
        self.assertNotIn("low_indoor", on(ev(lt=latest(floor=2.5))))

    def test_sensor_lost(self):
        d = ev(lt=latest(age=3 * 3600))
        self.assertIn("sensor_lost", on(d)); self.assertNotIn("overheat", d)
        lt = latest(); lt["다락"]["time_ms"] = (NOW - 3 * 3600) * 1000
        d = ev(lt=lt)
        self.assertIn("sensor_lost", on(d)); self.assertIn("다락", d["sensor_lost"][1])

    def test_forecast_caution_and_danger(self):
        d = ev(f=fc(-6))
        self.assertIn("forecast_frost", on(d))
        self.assertIn("동파 주의", d["forecast_frost"][1]); self.assertIn("-6.0°C", d["forecast_frost"][1])
        # 2단 1102W/UA47 → +23.4°C 여유: 주의만
        d = ev(st=status(stage=1, stage_w=200), f=fc(-1))   # 가상의 약한 히터: -1+4.3=3.3 < 5 → 위험
        self.assertIn("동파 위험", d["forecast_frost"][1]); self.assertIn("올리세요", d["forecast_frost"][1])
        self.assertNotIn("forecast_frost", on(ev(f=fc(-3))))

    def test_forecast_too_old_ignored(self):
        self.assertNotIn("forecast_frost", ev(f=fc(-10), mtime=NOW - 13 * 3600))

    def test_comfort_departure_hint(self):
        d = ev(st=status(mode="comfort"), f=fc(-8))
        self.assertIn("떠날 때 동파 모드 확인", d["forecast_frost"][1])

    def test_config_override(self):
        cfg = dict(CFG, notify={"overheat_c": {"내부1층": 25}})
        self.assertIn("overheat", on(ev(lt=latest(floor=25.5), cfg=cfg)))
        self.assertEqual(cw.ncfg(cfg)["overheat_c"]["다락"], 32.0)   # 나머지 기본값 유지


class Step(unittest.TestCase):
    def setUp(self):
        self.sent = []; self.ws = {}; self.N = cw.ncfg(CFG)

    def run_at(self, t, decisions):
        return cw.step(t, decisions, self.ws, self.N, self.sent.append)

    def test_first_remind_recover(self):
        hot = {"overheat": (True, "과열", "해소")}
        self.assertEqual(self.run_at(0, hot), ["과열"])
        self.assertEqual(self.run_at(60, hot), [])                       # 반복 억제
        self.assertEqual(self.run_at(30 * 60, hot), ["(계속) 과열"])     # 30분 뒤 다시
        self.assertEqual(self.run_at(31 * 60, {"overheat": (False, "과열", "해소")}), ["해소"])
        self.assertEqual(self.run_at(32 * 60, {"overheat": (False, "과열", "해소")}), [])

    def test_missing_key_keeps_state(self):
        self.run_at(0, {"heater_fault": (True, "고장", "복귀")})
        self.assertEqual(self.run_at(60, {}), [])                         # 제어기 멈춰 판단 불가 → 복귀 보내지 않음
        self.assertTrue(self.ws["heater_fault"]["on"])


if __name__ == "__main__":
    unittest.main()
