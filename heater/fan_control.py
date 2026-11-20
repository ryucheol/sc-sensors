#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""선풍기 제어(Tuya 플러그 + 기계식 선풍기 약 41 W, 위로 향하게) — cron 매분, 히터 제어기와 독립.

자동: 난방(comfort) 모드에서만, 다락−1층 온도차가 on_delta(1.0) 이상이면 켬, off_delta(0.3) 이하면 끔(띠 제어).
      히터가 꺼진 동안에도 돈다(실측: 히터가 꺼져 있을 때 성층이 더 큼 — 중앙값 0.9 vs 가동 중 0.6, 선풍기로 0.4).
      도착 예열(무인 comfort)에도 돌아 1층이 빨리 목표에 닿고 천장 쪽 열손실이 준다.
      취침(소음·다락 취침)·동파(무인, 히터 거의 안 돎)에선 끔. require_heater=true 면 히터 가동(+run_after_min분) 중만.
더위: 재실(presence_state.json, 휴대폰 핫스팟 감지)이고 히터가 꺼져 있을 때 1층 hot_on_c(28) 이상이면 켬,
      hot_off_c(27) 이하면 끔. 모든 모드(취침 포함) — 여름 밤 더위용. 무인일 땐 안 돎(선풍기는 공기를 식히지 않음).
여름·냉방 혼합: 재실이고 바깥(바닥외부)이 summer_out_c(22) 이상이면 모드와 무관하게(취침 포함) 성층 해소 규칙을 적용
      — 에어컨 냉기는 1층에 고이고 다락은 더우니 섞어 준다(에어컨 상태는 아직 모름 → 온도차로 판단).
수동: 텔레그램/시트 '선풍기 켜|꺼|자동' → fan_cmd.json. 켜/꺼는 manual_hold_h 시간 유지 후 자동으로 돌아감.
기록: 실제로 켜져 있으면 fan_on 파일을 만든다(열모델 로그의 fan 열이 이것을 읽음), 상태는 fan_status.json.
기기: tuya_keys.json 의 name "Fan"(type shelly 도 가능 — shelly_plug.make_device).
설정: heater_config.json "fan": {"enabled":true,"on_delta":1.0,"off_delta":0.3,"require_heater":false,"run_after_min":5,
                                  "modes":["comfort"],"manual_hold_h":4,"sensor_stale_min":10}
실행: ~/tuya-venv/bin/python heater/fan_control.py [--dry-run]   테스트: cd heater && python3 -m unittest test_fan_control
"""
import os, sys, json, time, datetime

HERE = os.path.dirname(os.path.realpath(__file__))
BASE = os.path.dirname(HERE)
sys.path.insert(0, HERE)
P = {k: os.path.join(BASE, v) for k, v in {
    "cfg": "heater_config.json", "keys": "tuya_keys.json", "status": "heater_status.json", "latest": "latest.json",
    "cmd": "fan_cmd.json", "state": "fan_state.json", "out": "fan_status.json", "flag": "fan_on", "log": "fan.log",
    "presence": "presence_state.json"}.items()}
DEFAULTS = {"enabled": True, "on_delta": 1.0, "off_delta": 0.3, "require_heater": False, "run_after_min": 5,
            "modes": ["comfort"], "manual_hold_h": 4, "sensor_stale_min": 10,
            "hot_on_c": 28.0, "hot_off_c": 27.0, "hot_need_presence": True, "summer_out_c": 22.0}


def load(path, default=None):
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return default


def log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    try:
        with open(P["log"], "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def cfg():
    c = dict(DEFAULTS)
    c.update((load(P["cfg"], {}) or {}).get("fan") or {})
    return c


def decide(now, c, status, temps, cmd, st, cur_on, present=None):
    """순수 판단 → (want_on, 이유, 새 상태). temps: {"floor": °C|None, "attic": °C|None} (오래된 값은 None).
    우선순위: 수동 > 더위(재실) > 성층 해소(난방 모드)."""
    st = dict(st or {})
    status = status or {}
    if status.get("relay"):
        st["heater_on_ts"] = now
    cmd = cmd or {}
    m, ts = cmd.get("mode"), cmd.get("ts", 0)
    if m in ("on", "off") and now - ts < c["manual_hold_h"] * 3600:
        left = (c["manual_hold_h"] * 3600 - (now - ts)) / 60.0
        return m == "on", "수동 %s(%.0f분 남음)" % ("켜기" if m == "on" else "끄기", left), st
    # 더위: 사람이 있고 히터가 꺼져 있을 때 1층이 hot_on_c 이상이면 켬, hot_off_c 이하면 끔(모든 모드, 취침 포함)
    f0 = temps.get("floor")
    hot = False
    if f0 is not None and not status.get("relay") and (present or not c.get("hot_need_presence", True)):
        hot = f0 > c["hot_off_c"] if st.get("hot") else f0 >= c["hot_on_c"]
    st["hot"] = hot
    if hot:
        return True, "자동(더위): 1층 %.1f°C (켬≥%.1f / 끔≤%.1f, 재실)" % (f0, c["hot_on_c"], c["hot_off_c"]), st
    mode = status.get("mode")
    o = temps.get("out")
    summer = bool(present) and o is not None and o >= c["summer_out_c"]   # 여름 재실(에어컨 냉기가 1층에 고임)
    if mode not in c["modes"] and not summer:
        return False, "자동: %s 모드라 끔" % (mode or "?"), st
    f, a = temps.get("floor"), temps.get("attic")
    if f is None or a is None:
        return False, "자동: 센서 값 없음/오래됨 → 끔", st
    d = round(a - f, 1)   # 센서 분해능 0.1°C — 부동소수 오차로 띠 경계가 흔들리지 않게
    if c.get("require_heater"):
        heat = bool(status.get("relay")) or (now - st.get("heater_on_ts", 0) <= c["run_after_min"] * 60)
        if not heat:
            return False, "자동: 히터 꺼짐(%d분 넘음), 차 %.1f" % (c["run_after_min"], d), st
    if cur_on:
        on = d > c["off_delta"]
    else:
        on = d >= c["on_delta"]
    return on, "자동%s: 다락−1층 %.1f°C (켬≥%.1f / 끔≤%.1f)" % (
        "(여름·냉방 혼합)" if summer and mode not in c["modes"] else "", d, c["on_delta"], c["off_delta"]), st


def temps_from(latest, now, stale_min):
    out = {}
    for k, name in (("floor", "내부1층"), ("attic", "다락"), ("out", "바닥외부")):
        e = (latest or {}).get(name) or {}
        t, ms = e.get("temp"), e.get("time_ms")
        out[k] = t if (t is not None and ms and now - ms / 1000.0 <= stale_min * 60) else None
    return out


def device():
    devs = load(P["keys"], []) or []
    dev = next((d for d in devs if d.get("name") == "Fan"), None)
    if not dev:
        return None
    from shelly_plug import make_device
    d = make_device(dev)
    try:
        d.set_socketTimeout(5); d.set_socketRetryLimit(2)
    except Exception:
        pass
    return d


def main(argv):
    dry = "--dry-run" in argv
    c = cfg()
    if not c.get("enabled", True):
        return 0
    now = time.time()
    dev = device()
    if dev is None:
        log("tuya_keys.json에 'Fan' 없음"); return 1
    try:
        s = dev.status() or {}
        cur = (s.get("dps") or {}).get("1")
    except Exception as e:
        s, cur = {"Error": str(e)}, None
    if cur is None:
        log("선풍기 플러그 응답 없음: %s" % s.get("Error"))
        import plug_alert; plug_alert.check("선풍기", False)
        return 1
    import plug_alert; plug_alert.check("선풍기", True)
    st = load(P["state"], {}) or {}
    want, why, st2 = decide(now, c, load(P["status"], {}), temps_from(load(P["latest"], {}), now, c["sensor_stale_min"]),
                            load(P["cmd"], {}), st, bool(cur),
                            present=(load(P["presence"], {}) or {}).get("present"))
    if dry:
        print("now=%s want=%s | %s" % (cur, want, why)); return 0
    actual = bool(cur)
    if want != actual:
        try:
            dev.turn_on() if want else dev.turn_off()
            actual = want
            log("선풍기 %s — %s" % ("켬" if want else "끔", why))
        except Exception as e:
            log("선풍기 %s 실패: %s" % ("켜기" if want else "끄기", e))
    if actual:
        open(P["flag"], "a").close()
    elif os.path.exists(P["flag"]):
        os.remove(P["flag"])
    json.dump(st2, open(P["state"], "w", encoding="utf-8"))
    json.dump({"updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "on": actual, "why": why},
              open(P["out"], "w", encoding="utf-8"), ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
