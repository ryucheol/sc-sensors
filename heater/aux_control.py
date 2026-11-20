#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""보조 히터(Tuya 'Secondary heater', 실측 약 470 W, 시끄러움) 자동 제어 — cron 매분, 메인 히터 제어기와 독립.

시끄러우니 사람이 있을 땐 손으로만 쓰고, 자동은 '무인일 때의 예비·보강'에 쓴다.
우선순위:
  1) 동파 위험: 1층 ≤ frost_on_c(2.5) → 켬, ≥ frost_off_c(4.0) → 끔.  (메인 3/5 아래 한 단계 — 메인 고장·한파 백업)
     모든 모드, 수동 '끄기'보다도 우선(동파가 소음보다 중요).
  2) 수동: '보조 켜|꺼|자동' (manual_hold_h 시간 유지 후 자동).
  3) 무인 예열 보강: 도착 예약으로 시작된 난방(source=arrival)에서 1층이 목표−boost_gap_c(1.0) 미만이면 켬,
     목표−0.3 이상이면 끔 → 사람이 오기 전 빨리 데움.
  4) 메인 고장 백업: 동파 모드인데 메인 플러그가 켜져 있으나 가열 안 됨(heater_ok=false)·통신 끊김이면 1층 < frost_off_c 동안 켬.
  그 밖(재실 난방·취침, 동파 평시)엔 끔.
자가 점검(11-20): 도착 예약이 있고 아직 예열 전(동파 모드)이며 도착까지 selftest_before_h(12) 안이면, 그 예약에 한 번
         selftest_s(20)초 켜서 전력을 잰다(재실·수동 끄기 중엔 안 함). 켜진 동안의 실측도 반영(≥ok_w 정상, <bad_w가 2분 → 안 됨).
         결과 heat_ok(True/False/None)를 aux_status.json에 쓰고, 메인 제어기는 heat_ok=False면 예열 계산에서 보조를 뺀다
         (본체 스위치만 꺼 두고 떠나도 늦게 데워지지 않게).
데드맨: 켜 둔 동안 Tuya 카운트다운(DP9)을 30분으로 5분마다 다시 걸어, Pi가 죽으면 30분 뒤 스스로 꺼짐
         (Pi 없을 때의 동파는 Shelly 가드가 메인 히터로 맡는다).
설정: heater_config.json "aux": {"enabled":true,"frost_on_c":2.5,"frost_off_c":4.0,"boost_gap_c":1.0,
                                  "manual_hold_h":2,"deadman_s":1800,"sensor_stale_min":15}
실행: ~/tuya-venv/bin/python heater/aux_control.py [--dry-run]   테스트: cd heater && python3 -m unittest test_aux_control
"""
import os, sys, json, time, datetime

HERE = os.path.dirname(os.path.realpath(__file__))
BASE = os.path.dirname(HERE)
sys.path.insert(0, HERE)
P = {k: os.path.join(BASE, v) for k, v in {
    "cfg": "heater_config.json", "keys": "tuya_keys.json", "state": "heater_state.json", "status": "heater_status.json",
    "latest": "latest.json", "cmd": "aux_cmd.json", "mine": "aux_state.json", "out": "aux_status.json",
    "log": "aux.log", "presence": "presence_state.json"}.items()}
DEFAULTS = {"enabled": True, "frost_on_c": 2.5, "frost_off_c": 4.0, "boost_gap_c": 1.0, "manual_hold_h": 2,
            "deadman_s": 1800, "sensor_stale_min": 15,
            "selftest_before_h": 12, "selftest_s": 20, "ok_w": 100, "bad_w": 50, "bad_after_s": 120}


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
    c.update((load(P["cfg"], {}) or {}).get("aux") or {})
    return c


def floor_temp(latest, now, stale_min):
    e = (latest or {}).get("내부1층") or {}
    t, ms = e.get("temp"), e.get("time_ms")
    return t if (t is not None and ms and now - ms / 1000.0 <= stale_min * 60) else None


def decide(now, c, state, status, t1, cmd, cur_on):
    """순수 판단 → (켤까, 이유). t1 = 1층(신선할 때만, 아니면 None)."""
    state, status, cmd = state or {}, status or {}, cmd or {}
    # 1) 동파 위험(최우선)
    if t1 is not None:
        if t1 <= c["frost_on_c"] or (cur_on and t1 < c["frost_off_c"]):   # 켜진 뒤엔 frost_off_c까지 유지
            return True, "동파 위험: 1층 %.1f°C (켬≤%.1f / 끔≥%.1f)" % (t1, c["frost_on_c"], c["frost_off_c"])
    # 2) 수동
    m, ts = cmd.get("mode"), cmd.get("ts", 0)
    if m in ("on", "off") and now - ts < c["manual_hold_h"] * 3600:
        return m == "on", "수동 %s(%.0f분 남음)" % ("켜기" if m == "on" else "끄기", (c["manual_hold_h"] * 3600 - (now - ts)) / 60)
    mode, src = state.get("mode"), state.get("source")
    # 3) 무인 예열 보강
    if mode == "comfort" and src == "arrival" and t1 is not None:
        target = state.get("on", 23)
        on = t1 < target - 0.3 if cur_on else t1 < target - c["boost_gap_c"]
        return on, "예열 보강(무인): 1층 %.1f°C, 목표 %.1f" % (t1, target)
    # 4) 메인 고장 백업(동파 모드)
    if mode == "frost" and t1 is not None and t1 < c["frost_off_c"] + 1:
        bad = (status.get("relay") and status.get("heater_ok") is False) or (status.get("status_fail_streak") or 0) >= 3
        if bad:
            return True, "메인 히터 이상 백업: 1층 %.1f°C" % t1
    return False, "자동: %s 모드 평시(보조는 무인 예비·보강용)" % ({"comfort": "난방", "sleep": "취침", "frost": "동파"}.get(mode, mode or "?"))


def selftest_due(now, c, state, cmd, present, mine):
    """자가 점검을 지금 할지(순수). 도착 예약이 있고, 아직 예열 전(comfort 아님), 도착까지 selftest_before_h 안,
       이 예약에 대해 아직 안 함, 재실 아님, 수동 '끄기' 유지 중 아님."""
    arr = (state or {}).get("arrival") or {}
    ts = arr.get("ts")
    if not ts or (state or {}).get("mode") == "comfort" or present:
        return False
    if not (0 < ts - now <= c["selftest_before_h"] * 3600):
        return False
    cmd = cmd or {}
    if cmd.get("mode") == "off" and now - cmd.get("ts", 0) < c["manual_hold_h"] * 3600:
        return False
    return (mine or {}).get("selftest_for") != ts


def observe(now, c, actual_on, watt, mine):
    """켜진 동안의 실측으로 heat_ok 갱신(순수, mine을 고쳐 반환). ≥ok_w면 정상, <bad_w가 bad_after_s 이어지면 안 됨."""
    mine = dict(mine or {})
    if not actual_on:
        mine.pop("low_since", None); return mine
    if watt >= c["ok_w"]:
        mine.update(heat_ok=True, heat_ok_ts=now); mine.pop("low_since", None)
    elif watt < c["bad_w"]:
        ls = mine.setdefault("low_since", now)
        if now - ls >= c["bad_after_s"]:
            mine.update(heat_ok=False, heat_ok_ts=now)
    return mine


def device():
    devs = load(P["keys"], []) or []
    dev = next((d for d in devs if d.get("name") == "Secondary heater"), None)
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
        log("tuya_keys.json에 'Secondary heater' 없음"); return 1
    try:
        s = dev.status() or {}
        dps = s.get("dps") or {}
        cur = dps.get("1")
    except Exception as e:
        s, dps, cur = {"Error": str(e)}, {}, None
    if cur is None:
        log("보조 히터 플러그 응답 없음: %s" % s.get("Error"))
        import plug_alert; plug_alert.check("보조 히터", False, "동파 백업·무인 예열 보강이 안 됨")
        return 1
    import plug_alert; plug_alert.check("보조 히터", True)
    t1 = floor_temp(load(P["latest"], {}), now, c["sensor_stale_min"])
    want, why = decide(now, c, load(P["state"], {}), load(P["status"], {}), t1, load(P["cmd"], {}), bool(cur))
    if dry:
        print("now=%s want=%s | %s" % (cur, want, why)); return 0
    mine = load(P["mine"], {}) or {}
    actual = bool(cur)
    try:
        if want and not actual:
            dev.turn_on(); actual = True; log("보조 히터 켬 — %s" % why)
        elif not want and actual:
            dev.turn_off(); actual = False; log("보조 히터 끔 — %s" % why)
        if actual and now - mine.get("dm_ts", 0) >= 300:      # 데드맨: 30분 카운트다운을 5분마다 다시
            dev.set_value(9, int(c["deadman_s"])); mine["dm_ts"] = now
        elif not actual and dps.get("9"):
            dev.set_value(9, 0); mine["dm_ts"] = 0             # 꺼져 있을 땐 카운트다운 해제(혼자 켜지지 않게)
    except Exception as e:
        log("보조 히터 제어 실패: %s" % e)
    w = (dps.get("19") or 0) / 10.0
    state = load(P["state"], {}) or {}
    present = (load(P["presence"], {}) or {}).get("present")
    if not actual and not want and selftest_due(now, c, state, load(P["cmd"], {}), present, mine):
        mine["selftest_for"] = (state.get("arrival") or {}).get("ts")
        try:
            dev.turn_on(); time.sleep(c["selftest_s"])
            w_t = ((dev.status() or {}).get("dps") or {}).get("19")
            w_t = (w_t or 0) / 10.0
            dev.turn_off()
            ok = w_t >= c["ok_w"]
            mine.update(heat_ok=ok, heat_ok_ts=time.time(), selftest_w=w_t)
            log("보조 히터 자가 점검(도착 예약 전): %.0fW → %s" % (w_t, "정상" if ok else "가열 안 됨 — 예열 계산에서 제외(본체 스위치·콘센트 확인)"))
            if not ok:
                try:
                    sys.path.insert(0, os.path.join(BASE, "notify"))
                    import telegram_notify as tg
                    tg.send("보조 히터 자가 점검: 켜도 %.0fW — 본체 스위치가 꺼져 있는 듯합니다. 도착 예열은 메인 히터만으로 계산해 더 일찍 시작합니다." % w_t,
                            key="aux_selftest", min_interval=6 * 3600)
                except Exception:
                    pass
        except Exception as e:
            try: dev.turn_off()
            except Exception: pass
            log("보조 히터 자가 점검 실패: %s" % e)
    else:
        mine = observe(now, c, actual, w, mine)
    json.dump(mine, open(P["mine"], "w"))
    json.dump({"updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "on": actual, "why": why, "watt": w,
               "heat_ok": mine.get("heat_ok"), "heat_ok_ts": mine.get("heat_ok_ts")},
              open(P["out"], "w", encoding="utf-8"), ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
