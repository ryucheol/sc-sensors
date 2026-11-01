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
STATUS = os.path.join(BASE, "heater_status.json")    # 계측·전력량·고장 스냅샷(서비스만 기록, 제어상태와 분리)
ENERGY_CSV = os.path.join(BASE, "heater_energy.csv") # 시간별 열모델 원시데이터(오프라인 UA/C 피팅용)

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
    # ── 계측 누적(heater_status.json에서 복원; 제어와 독립. 재시작 시 acct_last=None → 첫 주기 적분 안 함) ──
    _ss = load(STATUS, {})
    total_wh = _ss.get("total_wh", 0.0); day_wh = _ss.get("day_wh", 0.0)
    day_date = _ss.get("day_date", datetime.date.today().isoformat())
    acct_last = None
    hr = {"key": None, "wdt": 0.0, "tin_dt": 0.0, "tout_dt": 0.0, "dt": 0.0, "on_dt": 0.0, "tin0": None, "tin1": None}
    fault_dt = 0.0; fault_active = False; fail_streak = 0
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
            cur_relay = st.get("relay"); cur_watt = 0.0; cur_volt = 0.0; cur_kwh_hw = None; status_ok = False
            try:
                s = d.status().get("dps", {})
                cur_relay = bool(s.get("1")); cur_watt = s.get("19", 0) / 10.0; cur_volt = s.get("20", 0) / 10.0
                _e17 = s.get("17"); cur_kwh_hw = (_e17 / 100.0) if _e17 is not None else None   # DP17: 플러그 하드웨어 누적(0.01kWh 단위)
                status_ok = True; fail_streak = 0
                if cur_relay != st.get("relay"): st["relay"] = cur_relay; save_atomic(STATE, st)
            except Exception as e:
                fail_streak += 1
                log("상태조회 오류(%d연속): %s" % (fail_streak, e))

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
                    cur_relay = relay; cur_watt = watt    # 스냅샷·고장감지가 변경 직후 실측을 쓰도록 갱신
                    log("%s %s°C [%s %s/%s] → 플러그 %s (%.0fW %.0fV)"
                        % (cfg["sensor"], t_in, st["mode"], on_th, off_th, "ON" if relay else "OFF", watt, volt))
                elif now() - last_hb > 1800:    # 30분 하트비트(현황 로그)
                    log("현황: %s %s°C 외기 %s°C [%s %s/%s] 플러그 %s (%.0fW)"
                        % (cfg["sensor"], t_in, t_out, st["mode"], on_th, off_th, "ON" if cur_relay else "OFF", cur_watt))
                    last_hb = now()
            except Exception as e:
                log("플러그 제어 오류: %s" % e)

            # ── 계측·축적(제어와 완전 분리: 여기서 예외가 나도 제어엔 영향 없음) ──
            try:
                tnow = now()
                dt = (tnow - acct_last) if acct_last else 0.0
                acct_last = tnow
                if dt < 0 or dt > 2 * poll: dt = 0.0           # 재시작/다운타임 후 stale 적분 방지
                # 전력량 적분(상태조회 성공 시만)
                if status_ok and dt > 0:
                    wh = cur_watt * dt / 3600.0; total_wh += wh; day_wh += wh
                # 일일 롤오버
                today = datetime.date.today().isoformat()
                if today != day_date:
                    log("일일 전력량 %s: %.2f kWh (누적 %.1f kWh)" % (day_date, day_wh / 1000.0, total_wh / 1000.0))
                    day_wh = 0.0; day_date = today
                # 시간별 열모델 원시데이터(오프라인 UA/C 피팅용)
                if status_ok and dt > 0 and not stale and t_in is not None:
                    hk = datetime.datetime.now().strftime("%Y-%m-%d %H")
                    if hr["key"] is None: hr["key"] = hk; hr["tin0"] = t_in
                    if hk != hr["key"]:
                        if hr["dt"] > 0:
                            row = "%s:00,%.1f,%.2f,%.2f,%.2f,%.2f,%.2f\n" % (
                                hr["key"], hr["wdt"] / 3600.0, hr["tin_dt"] / hr["dt"], hr["tout_dt"] / hr["dt"],
                                hr["tin0"] if hr["tin0"] is not None else t_in,
                                hr["tin1"] if hr["tin1"] is not None else t_in, hr["on_dt"] / hr["dt"])
                            try:
                                newf = not os.path.exists(ENERGY_CSV)
                                with open(ENERGY_CSV, "a") as f:
                                    if newf: f.write("hour,Wh,mean_Tin,mean_Tout,Tin_start,Tin_end,on_fraction\n")
                                    f.write(row)
                            except Exception: pass
                        hr = {"key": hk, "wdt": 0.0, "tin_dt": 0.0, "tout_dt": 0.0, "dt": 0.0, "on_dt": 0.0, "tin0": t_in, "tin1": t_in}
                    hr["wdt"] += cur_watt * dt; hr["tin_dt"] += t_in * dt
                    hr["tout_dt"] += (t_out if t_out is not None else 0.0) * dt
                    hr["dt"] += dt; hr["tin1"] = t_in
                    if cur_relay: hr["on_dt"] += dt
                # 히터 가열실패 감지(기대가열인데 전력 없음) — 로그만(업링크 없어 경보는 KT 후)
                wmin = cfg.get("fault_min_watt", 100.0); grace = cfg.get("fault_grace_sec", 300.0)
                expecting = status_ok and (not stale) and (t_in is not None) and (t_in < on_th) and cur_relay
                if expecting and cur_watt < wmin:
                    fault_dt += dt
                    if fault_dt >= grace and not fault_active:
                        fault_active = True
                        log("⚠ 히터 가열 안됨: 켜짐인데 %.0fW (실내 %.1f°C<%s). 히터 자체 스위치/다이얼/콘센트 확인" % (cur_watt, t_in, on_th))
                else:
                    if fault_active and status_ok and cur_watt >= wmin: log("히터 정상 복귀(%.0fW)" % cur_watt)
                    if not (expecting and cur_watt < wmin): fault_dt = 0.0; fault_active = False
                # 스냅샷(heater_status.json — 서비스만 기록)
                save_atomic(STATUS, {
                    "updated": stamp(), "mode": st.get("mode"), "relay": cur_relay,
                    "watt": round(cur_watt, 0), "volt": round(cur_volt, 1),
                    "hw_kwh": cur_kwh_hw, "today_kwh": round(day_wh / 1000.0, 3), "total_kwh": round(total_wh / 1000.0, 2),
                    "heater_ok": (not fault_active), "status_fail_streak": fail_streak,
                    "day_wh": round(day_wh, 1), "total_wh": round(total_wh, 1), "day_date": day_date})
            except Exception as e:
                log("계측 오류: %s" % e)
        except Exception as e:
            log("루프 오류: %s" % e)
        time.sleep(poll)

if __name__ == "__main__":
    main()
