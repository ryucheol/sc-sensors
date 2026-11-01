#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""농막 히터 제어 — 내부1층 온도 기반 동파/난방 히스테리시스 + 금요일 적응형 예열.
   venv(~/tuya-venv)로 실행. 게이트웨이가 쓰는 latest.json에서 온도를 읽고 Tuya 플러그를 로컬(LAN) 제어.
   설정: heater_config.json (즉시 반영) / 모드·임계값: heater_state.json (난방/동파 명령이 갱신)."""
import os, json, time, math, datetime

BASE   = os.path.expanduser("~/smart-cabin")
CFG    = os.path.join(BASE, "heater_config.json")
STATE  = os.path.join(BASE, "heater_state.json")
KEYS   = os.path.join(BASE, "tuya_keys.json")
LATEST = os.path.join(BASE, "latest.json")
LOG    = os.path.join(BASE, "heater.log")

import tinytuya

def now(): return time.time()
def stamp(): return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def log(msg):
    line = "[%s] %s" % (stamp(), msg)
    print(line, flush=True)
    try:
        with open(LOG, "a") as f: f.write(line + "\n")
    except Exception: pass

def load(path, default):
    try:
        with open(path) as f: return json.load(f)
    except Exception: return default

def save_atomic(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f: json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)

def read_temp(sensor):
    data = load(LATEST, {})
    rec = data.get(sensor) if isinstance(data, dict) else None
    if not rec: return None, None
    return rec.get("temp"), rec.get("time_ms")

def get_device():
    devs = load(KEYS, [])
    for d in devs:
        if d.get("name") == "Main heater": return d
    return devs[0] if devs else None

def preheat_lead_hours(cfg, t_in, t_out):
    p = cfg["preheat"]
    tgt, tau, ua, P = p["target_temp"], p["tau_hours"], p["ua_w_per_k"], p["heater_watts"]
    margin, maxlead = p["margin"], p["max_lead_hours"]
    if t_in is None:        return maxlead          # 온도 모르면 안전하게 최대 선행
    if t_in >= tgt:         return 0.0
    if t_out is None: t_out = 0.0                    # 외기 모르면 0°C 가정(보수적)
    t_eq = t_out + P / ua
    if t_eq <= tgt + 1:     return maxlead           # 그 외기선 목표 도달 어려움 → 최대 선행
    lead = tau * math.log((t_eq - t_in) / (t_eq - tgt)) * margin
    return max(0.0, min(lead, maxlead))

def default_state(cfg):
    f = cfg["frost"]
    return {"mode":"frost","on":f["on"],"off":f["off"],"source":"default",
            "since":now(),"last_cmd_ts":0,"relay":None}

def apply_relay(d, want):
    (d.turn_on() if want else d.turn_off())
    time.sleep(1)
    s = d.status().get("dps", {})
    return bool(s.get("1")), s.get("19",0)/10.0, s.get("20",0)/10.0   # relay, W, V

def main():
    dev = get_device()
    if not dev:
        log("tuya_keys.json에 기기 없음 — 종료"); return
    d = tinytuya.OutletDevice(dev["id"], dev["ip"], dev["key"])
    d.set_version(float(dev["version"])); d.set_socketTimeout(5)
    log("히터 제어 시작: %s @ %s v%s" % (dev["name"], dev["ip"], dev["version"]))

    last_hb = 0; last_stale_log = 0
    while True:
        poll = 60
        try:
            cfg = load(CFG, None)
            if not cfg: log("설정파일 없음"); time.sleep(30); continue
            poll = cfg.get("poll_sec", 60)
            st = load(STATE, None)
            if not st: st = default_state(cfg); save_atomic(STATE, st)

            t_in, t_ms = read_temp(cfg["sensor"])
            t_out, _   = read_temp(cfg["preheat"].get("outdoor_sensor", ""))
            stale = (t_ms is None) or ((now()*1000 - t_ms) > cfg["stale_temp_min"]*60*1000)

            # ── 스케줄: 금요일 적응형 예열 / 노쇼 복귀 / 안전 백스톱 ──
            dtn = datetime.datetime.now(); p = cfg["preheat"]; changed = False
            if p.get("enabled") and dtn.weekday() == p["weekday"]:
                target = dtn.replace(hour=p["target_hour"], minute=p["target_min"], second=0, microsecond=0)
                noshow = dtn.replace(hour=0, minute=0, second=0, microsecond=0) + datetime.timedelta(hours=p["noshow_hour"])
                if dtn < target and st["mode"] == "frost":
                    lead = preheat_lead_hours(cfg, t_in, t_out)
                    if dtn >= target - datetime.timedelta(hours=lead):
                        c = cfg["comfort"]
                        st.update(mode="comfort", on=c["on"], off=c["off"], source="preheat", since=now()); changed = True
                        log("예열 시작: 목표 %s°C@%02d:%02d, 내부 %s°C 외기 %s°C, 선행 %.1fh → 난방 %s/%s"
                            % (p["target_temp"], p["target_hour"], p["target_min"], t_in, t_out, lead, c["on"], c["off"]))
                if dtn >= noshow and st["source"] == "preheat":
                    f = cfg["frost"]; st.update(mode="frost", on=f["on"], off=f["off"], source="noshow", since=now()); changed = True
                    log("노쇼(난방 명령 없음) → 동파 복귀")
            if st["mode"] == "comfort" and st.get("source") == "cmd" and st.get("last_cmd_ts", 0) > 0:
                if (now() - st["last_cmd_ts"]) > cfg["backstop_comfort_hours"]*3600:
                    f = cfg["frost"]; st.update(mode="frost", on=f["on"], off=f["off"], source="backstop", since=now()); changed = True
                    log("안전 백스톱: 난방 %sh 무신호 → 동파 복귀" % cfg["backstop_comfort_hours"])
            if changed: save_atomic(STATE, st)

            # ── 실제 플러그 상태 읽기(앱·수동·정전복구 등 외부변경 자가보정) ──
            cur_relay = st.get("relay"); cur_watt = 0.0
            try:
                s = d.status().get("dps", {})
                cur_relay = bool(s.get("1")); cur_watt = s.get("19", 0) / 10.0
                if cur_relay != st.get("relay"): st["relay"] = cur_relay; save_atomic(STATE, st)
            except Exception as e:
                log("상태조회 오류: %s" % e)

            # ── 히스테리시스 제어 ──
            on_th, off_th = st["on"], st["off"]
            if stale:
                want = True if st["mode"] == "frost" else cur_relay   # 온도미상: 동파면 안전가동
                if now() - last_stale_log > 1800:
                    log("⚠ 온도 데이터 오래됨(%s) → %s" % (cfg["sensor"], "안전가동(ON)" if st["mode"]=="frost" else "현상유지"))
                    last_stale_log = now()
            else:
                if   t_in < on_th:  want = True
                elif t_in > off_th: want = False
                else:               want = cur_relay    # 밴드 내 유지

            try:
                if want is not None and want != cur_relay:
                    relay, watt, volt = apply_relay(d, want)
                    st["relay"] = relay; save_atomic(STATE, st)
                    log("%s %s°C [%s %s/%s] → 플러그 %s (%.0fW %.0fV)"
                        % (cfg["sensor"], t_in, st["mode"], on_th, off_th, "ON" if relay else "OFF", watt, volt))
                elif now() - last_hb > 1800:    # 30분 하트비트(현황 로그)
                    log("현황: %s %s°C 외기 %s°C [%s %s/%s] 플러그 %s (%.0fW)"
                        % (cfg["sensor"], t_in, t_out, st["mode"], on_th, off_th, "ON" if cur_relay else "OFF", cur_watt))
                    last_hb = now()
            except Exception as e:
                log("플러그 제어 오류: %s" % e)
        except Exception as e:
            log("루프 오류: %s" % e)
        time.sleep(poll)

if __name__ == "__main__":
    main()
