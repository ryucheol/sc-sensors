#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""농막 히터 제어 — 동파/난방/취침 히스테리시스 + 금요일 적응형 예열.
   제어센서는 mode에서 유도: 취침=다락, 그 외=기본(내부1층). 계측(전력량·열모델)은 항상 1층 기준.
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
THERMAL_CSV = os.path.join(BASE, "thermal_log.csv")  # 1분 열모델 원시데이터(2노드 그레이박스 학습용, gitignore)
_THERMAL_COLS = "ts,mode,relay,watt,volt,t1,h1,t2,h2,to,ho,t_ctl,delta,fan\n"

def thermal_log(mode, relay, watt, volt, t_ctl, delta, floor_s, attic_s, out_s):
    """1분마다 한 줄: 센서 3개 온·습도 + 히터 상태. 학습은 오프라인(scp 후 피팅). 실패해도 제어엔 무영향."""
    try:
        lat = load(LATEST, {}) or {}
        def g(s, k):
            v = lat.get(s, {}).get(k) if s else None
            return "" if v is None else v
        new = not os.path.exists(THERMAL_CSV)
        with open(THERMAL_CSV, "a") as f:
            if new: f.write(_THERMAL_COLS)
            f.write("%s,%s,%d,%.0f,%.0f,%s,%s,%s,%s,%s,%s,%s,%.2f,\n" % (
                stamp(), mode or "", 1 if relay else 0, watt or 0, volt or 0,
                g(floor_s, "temp"), g(floor_s, "hum"), g(attic_s, "temp"), g(attic_s, "hum"),
                g(out_s, "temp"), g(out_s, "hum"), "" if t_ctl is None else t_ctl, delta))
    except Exception:
        pass

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

def schedule_step(cfg, st, t_in, t_out, dtn, tnow):
    """스케줄 판단(순수): 금요일 예열 / 노쇼 복귀 / 안전 백스톱. st를 제자리 수정, (changed, 로그메시지목록) 반환."""
    p = cfg["preheat"]; changed = False; logs = []
    if p.get("enabled") and dtn.weekday() == p["weekday"]:
        target = dtn.replace(hour=p["target_hour"], minute=p["target_min"], second=0, microsecond=0)
        if dtn < target and st["mode"] == "frost":
            lead = preheat_lead_hours(cfg, t_in, t_out)
            if dtn >= target - datetime.timedelta(hours=lead):
                c = cfg["comfort"]
                st.update(mode="comfort", on=c["on"], off=c["off"], source="preheat", since=tnow); changed = True
                logs.append("예열 시작: 목표 %s°C@%02d:%02d, 내부 %s°C 외기 %s°C, 선행 %.1fh → 난방 %s/%s"
                            % (p["target_temp"], p["target_hour"], p["target_min"], t_in, t_out, lead, c["on"], c["off"]))
    # 노쇼 복귀: 예열(source=preheat)이 시작된 날 0시 + noshow_hour 까지 '난방' 명령이 없으면 동파 복귀.
    # 금요일 블록 밖에서 판정해야 한다(noshow=토 00:00이라 금요일엔 영원히 참이 될 수 없음).
    if p.get("enabled") and st.get("source") == "preheat":
        try: base = datetime.datetime.fromtimestamp(st.get("since", tnow))
        except Exception: base = dtn
        noshow_at = base.replace(hour=0, minute=0, second=0, microsecond=0) + datetime.timedelta(hours=p["noshow_hour"])
        if dtn >= noshow_at:
            f = cfg["frost"]; st.update(mode="frost", on=f["on"], off=f["off"], source="noshow", since=tnow); changed = True
            logs.append("노쇼(난방 명령 없음) → 동파 복귀")
    if st["mode"] in ("comfort", "sleep") and st.get("source") == "cmd" and st.get("last_cmd_ts", 0) > 0:
        if (tnow - st["last_cmd_ts"]) > cfg["backstop_comfort_hours"]*3600:
            f = cfg["frost"]; st.update(mode="frost", on=f["on"], off=f["off"], source="backstop", since=tnow); changed = True
            logs.append("안전 백스톱: 재실모드 %sh 무신호 → 동파 복귀" % cfg["backstop_comfort_hours"])
    return changed, logs

def control_sensor(cfg, mode):
    """제어센서는 mode에서 유도(취침=다락, 그 외=기본)."""
    if mode == "sleep":
        return cfg.get("sleep", {}).get("sensor", cfg["sensor"])
    return cfg["sensor"]

# ── 실내 센서 이중화(1층 ↔ 다락) ─────────────────────────────────────────────
# 실내 센서 둘은 열성층화로 차이가 있다(실측: 난방 중 다락이 ~2°C, 난방 끈 뒤 밤엔 ~4°C 더 따뜻).
# 둘 다 살아 있을 때 차이 delta(=다락−1층)를 천천히 학습해 두고, 한쪽이 죽으면 다른 쪽 + delta 로 대신한다.
RED_DEFAULT = {"delta_init": 3.0, "tau_min": 60, "fallback_margin_frost": 1.5,
               "disagree_c": 5.0, "valid_min": -30.0, "valid_max": 60.0}

def red_cfg(cfg):
    r = dict(RED_DEFAULT); r.update(cfg.get("redundancy", {})); return r

def indoor_pair(cfg):
    """(기본센서=1층, 보조센서=다락). 다락 미설정이면 보조 없음."""
    a = cfg["sensor"]; b = cfg.get("sleep", {}).get("sensor")
    return a, (b if b and b != a else None)

def sensor_ok(temp, t_ms, now_ms, stale_ms, vmin=-30.0, vmax=60.0):
    """센서값 사용가능(순수): 값 있음 + 신선함 + 물리적으로 그럴듯한 범위."""
    if temp is None or t_ms is None: return False
    if (now_ms - t_ms) > stale_ms:   return False
    return vmin <= temp <= vmax

def update_delta(delta, t_attic, t_floor, dt, tau_s, clamp=10.0):
    """delta(=다락−1층) 지수이동평균 갱신(순수). 둘 다 정상일 때만 부를 것."""
    if dt <= 0: return delta
    a = min(1.0, dt / float(tau_s))
    d = delta + a * ((t_attic - t_floor) - delta)
    return max(-clamp, min(clamp, d))

def fuse_control(mode, t_ctl, ctl_ok, t_oth, oth_ok, d, r):
    """제어온도 결정(순수). d = 기대차(제어센서 − 다른센서).
       반환 (온도 또는 None, 출처). 출처: primary / primary-only / fallback / disagree / disagree-min / none.
       동파모드에선 항상 '더 춥게' 보는 쪽(=더 일찍 켜는 쪽)으로 치우친다."""
    margin = r["fallback_margin_frost"] if mode == "frost" else 0.0
    if ctl_ok and oth_ok:
        est = t_oth + d
        if abs(t_ctl - est) > r["disagree_c"]:
            if mode == "frost": return min(t_ctl, est - margin), "disagree-min"
            return t_ctl, "disagree"
        return t_ctl, "primary"
    if ctl_ok: return t_ctl, "primary-only"
    if oth_ok: return t_oth + d - margin, "fallback"
    return None, "none"

# ── 데드맨(fail-on): Pi·공유기가 죽으면 플러그가 스스로 켜지게 ─────────────────────
# 플러그 카운트다운(DP9)은 '현재 상태를 뒤집는다'(2026-11-04 집 플러그로 확인: 꺼짐→켜짐, 삭제·재설정 가능).
# 꺼져 있는 동안 sec(30분) 카운트다운을 rearm_sec(5분)마다 다시 걸어 계속 미룬다 → Pi가 살아 있으면 안 터진다.
# 켜질 땐 반드시 지운다(남아 있으면 sec 뒤 꺼져 버림). 히터 다이얼(최대)이 켜짐 고착 시 상한.
DM_DEFAULT = {"enabled": True, "dp": 9, "sec": 1800, "rearm_sec": 300}

def dm_cfg(cfg):
    r = dict(DM_DEFAULT); r.update(cfg.get("deadman", {})); return r

def deadman_action(relay, cd_reported, armed_age, dm):
    """데드맨 판단(순수). 반환 ("arm", 초) / ("clear", 0) / None.
       relay=실측 릴레이(None=모름), cd_reported=플러그가 보고한 남은 초(None=보고 없음),
       armed_age=마지막으로 건 뒤 지난 초(None=건 적 없음)."""
    if not dm.get("enabled") or relay is None:
        return None
    if relay:
        return ("clear", 0) if (cd_reported or armed_age is not None) else None
    if armed_age is None or armed_age >= dm["rearm_sec"] or cd_reported == 0:
        return ("arm", int(dm["sec"]))
    return None

def decide_relay(mode, on_th, off_th, t_ctl, ctl_stale, cur_relay):
    """히스테리시스 판단(순수): 원하는 릴레이 상태. 온도미상이면 동파=ON, 그 외=현상유지."""
    if ctl_stale:
        return True if mode == "frost" else cur_relay
    if t_ctl <= on_th: return True   # 경계값(=on_th) 보고 시 즉시 ON (값 격자 0.5°C+느린 갱신 보정)
    if t_ctl >= off_th: return False  # 경계값(=off_th) 보고 시 즉시 OFF → 오버슈트·초과가열 감소
    return cur_relay

def apply_relay(d, want):
    (d.turn_on() if want else d.turn_off())
    time.sleep(1)
    s = d.status().get("dps", {})
    return bool(s.get("1")), s.get("19",0)/10.0, s.get("20",0)/10.0   # relay, W, V

def main():
    import tinytuya    # 지연 임포트: 테스트가 tinytuya 없이 순수 로직을 임포트할 수 있게
    dev = get_device()
    if not dev:
        log("tuya_keys.json에 기기 없음 — 종료"); return
    d = tinytuya.OutletDevice(dev["id"], dev["ip"], dev["key"])
    d.set_version(float(dev["version"])); d.set_socketTimeout(5)
    log("히터 제어 시작: %s @ %s v%s" % (dev["name"], dev["ip"], dev["version"]))

    last_hb = 0; last_stale_log = 0
    dm_armed_ts = None; dm_err_log = 0; dm_logged = False
    # ── 계측 누적(heater_status.json에서 복원; 제어와 독립. 재시작 시 acct_last=None → 첫 주기 적분 안 함) ──
    _ss = load(STATUS, {})
    total_wh = _ss.get("total_wh", 0.0); day_wh = _ss.get("day_wh", 0.0)
    day_date = _ss.get("day_date", datetime.date.today().isoformat())
    acct_last = None
    hr = {"key": None, "wdt": 0.0, "tin_dt": 0.0, "tout_dt": 0.0, "dt": 0.0, "on_dt": 0.0, "tin0": None, "tin1": None}
    fault_dt = 0.0; fault_active = False; fail_streak = 0
    # ── 센서 이중화 상태: delta(=다락−1층) 학습값은 재시작해도 이어가도록 status에서 복원 ──
    delta = _ss.get("delta_attic_floor", red_cfg(load(CFG, {}) or {})["delta_init"])
    red_last = None; last_src = None; last_sensor_warn = {}
    prev_mode = None   # 모드 전환을 정확한 시각으로 로깅(현황 30분 간격 보완)
    while True:
        poll = 60
        try:
            cfg = load(CFG, None)
            if not cfg: log("설정파일 없음"); time.sleep(30); continue
            poll = cfg.get("poll_sec", 60)
            st = load(STATE, None)
            if not st: st = default_state(cfg); save_atomic(STATE, st)

            R = red_cfg(cfg); now_ms = now() * 1000; stale_ms = cfg["stale_temp_min"] * 60 * 1000
            def _ok(t, ms): return sensor_ok(t, ms, now_ms, stale_ms, R["valid_min"], R["valid_max"])
            floor_s, attic_s = indoor_pair(cfg)
            t_in, t_ms = read_temp(floor_s)
            stale = not _ok(t_in, t_ms)                       # 계측(전력량·열모델)은 1층 '실측'만 사용
            t_at, ta_ms = read_temp(attic_s) if attic_s else (None, None)
            at_ok = bool(attic_s) and _ok(t_at, ta_ms)
            t_out, to_ms = read_temp(cfg["preheat"].get("outdoor_sensor", ""))
            if not _ok(t_out, to_ms): t_out = None            # 외기 미상 → 예열은 0°C 가정(기존 동작)

            # ── 이중화: 둘 다 정상이면 delta 학습, 센서 이상은 30분마다 경고 ──
            _t = now(); _dtr = (_t - red_last) if red_last else 0.0; red_last = _t
            if _dtr < 0 or _dtr > 2 * poll: _dtr = 0.0
            # 둘이 크게 어긋나면(한쪽 고장 의심) 학습 중단 — 고장값이 delta에 스며드는 것 방지
            if (not stale) and at_ok and abs((t_at - t_in) - delta) <= R["disagree_c"]:
                delta = update_delta(delta, t_at, t_in, _dtr, R["tau_min"] * 60)
            for _nm, _good, _tv in ((floor_s, not stale, t_in), (attic_s, at_ok, t_at)):
                if _nm and not _good and _t - last_sensor_warn.get(_nm, 0) > 1800:
                    log("⚠ 센서 이상(%s): %s" % (_nm, "수신 끊김" if _tv is None else "오래됐거나 범위 밖(%s°C)" % _tv))
                    last_sensor_warn[_nm] = _t
            # 예열 판단용 1층 온도: 1층이 죽어도 다락+delta로 추정(보수적=더 춥게)
            t_floor_est, _ = fuse_control("frost", t_in, not stale, t_at, at_ok, -delta, R)

            # ── 스케줄: 금요일 적응형 예열 / 노쇼 복귀 / 안전 백스톱 ──
            changed, slogs = schedule_step(cfg, st, t_floor_est, t_out, datetime.datetime.now(), now())
            for m in slogs: log(m)
            if changed: save_atomic(STATE, st)

            # ── 제어 센서 선택: mode에서 유도(취침=다락, 그 외=기본). state엔 저장 안 함 ──
            #    (백스톱 등으로 mode가 바뀌면 센서도 자동 복귀. 계측용 t_in/stale은 항상 기본센서=1층 유지) ──
            ctl_sensor = control_sensor(cfg, st["mode"])
            if ctl_sensor == floor_s:
                c_t, c_ok, o_t, o_ok, d_exp = t_in, not stale, t_at, at_ok, -delta
            elif ctl_sensor == attic_s:
                c_t, c_ok, o_t, o_ok, d_exp = t_at, at_ok, t_in, not stale, delta
            else:
                c_t, c_ms = read_temp(ctl_sensor); c_ok = _ok(c_t, c_ms); o_t, o_ok, d_exp = None, False, 0.0
            t_ctl, ctl_src = fuse_control(st["mode"], c_t, c_ok, o_t, o_ok, d_exp, R)
            ctl_stale = t_ctl is None
            if t_ctl is not None: t_ctl = round(t_ctl, 1)
            if ctl_src != last_src:
                if last_src is not None or ctl_src != "primary":
                    log("제어온도 출처: %s → %s (%s 기준, 다락−1층 %.1f°C)" % (last_src, ctl_src, ctl_sensor, delta))
                last_src = ctl_src

            # ── 실제 플러그 상태 읽기(앱·수동·정전복구 등 외부변경 자가보정) ──
            cur_relay = st.get("relay"); cur_watt = 0.0; cur_volt = 0.0; cur_kwh_hw = None; status_ok = False
            DM = dm_cfg(cfg); cur_cd = None
            try:
                s = d.status().get("dps", {})
                cur_cd = s.get(str(DM["dp"]))
                cur_relay = bool(s.get("1")); cur_watt = s.get("19", 0) / 10.0; cur_volt = s.get("20", 0) / 10.0
                _e17 = s.get("17"); cur_kwh_hw = (_e17 / 100.0) if _e17 is not None else None   # DP17: 플러그 하드웨어 누적(0.01kWh 단위)
                status_ok = True; fail_streak = 0
                if cur_relay != st.get("relay"): st["relay"] = cur_relay; save_atomic(STATE, st)
            except Exception as e:
                fail_streak += 1
                log("상태조회 오류(%d연속): %s" % (fail_streak, e))

            # ── 히스테리시스 제어(제어센서 ctl_sensor/t_ctl 기준) ──
            on_th, off_th = st["on"], st["off"]
            # ── 모드 전환 정확 시각 로깅(현황 30분 간격 보완) ──
            if st["mode"] != prev_mode:
                if prev_mode is None:
                    log("모드 시작: %s [%s/%s] %s %s°C (source=%s)"
                        % (st["mode"], on_th, off_th, ctl_sensor, t_ctl, st.get("source")))
                else:
                    log("모드 전환: %s → %s [%s/%s] %s %s°C (source=%s)"
                        % (prev_mode, st["mode"], on_th, off_th, ctl_sensor, t_ctl, st.get("source")))
                prev_mode = st["mode"]
            want = decide_relay(st["mode"], on_th, off_th, t_ctl, ctl_stale, cur_relay)
            if ctl_stale and now() - last_stale_log > 1800:
                log("⚠ 온도 데이터 오래됨(%s) → %s" % (ctl_sensor, "안전가동(ON)" if st["mode"]=="frost" else "현상유지"))
                last_stale_log = now()

            try:
                if want is not None and want != cur_relay:
                    relay, watt, volt = apply_relay(d, want)
                    st["relay"] = relay; save_atomic(STATE, st)
                    cur_relay = relay; cur_watt = watt    # 스냅샷·고장감지가 변경 직후 실측을 쓰도록 갱신
                    log("%s %s°C(%s) [%s %s/%s] → 플러그 %s (%.0fW %.0fV)"
                        % (ctl_sensor, t_ctl, ctl_src, st["mode"], on_th, off_th, "ON" if relay else "OFF", watt, volt))
                elif now() - last_hb > 1800:    # 30분 하트비트(현황 로그)
                    log("현황: %s %s°C 외기 %s°C [%s %s/%s] 플러그 %s (%.0fW)"
                        % (ctl_sensor, t_ctl, t_out, st["mode"], on_th, off_th, "ON" if cur_relay else "OFF", cur_watt))
                    last_hb = now()
            except Exception as e:
                log("플러그 제어 오류: %s" % e)

            # ── 데드맨: 꺼짐이면 30분 뒤 켜짐을 계속 미루고, 켜짐이면 지운다 ──
            if status_ok:
                try:
                    _age = (now() - dm_armed_ts) if dm_armed_ts else None
                    act = deadman_action(cur_relay, cur_cd, _age, DM)
                    if act:
                        r = d.set_value(DM["dp"], act[1])
                        if isinstance(r, dict) and r.get("Error"): raise RuntimeError(r.get("Error"))
                        if act[0] == "arm":
                            dm_armed_ts = now()
                            if not dm_logged:
                                log("데드맨 작동: 꺼짐 동안 %d분 카운트다운을 %d분마다 연장(DP%s)" % (DM["sec"] // 60, DM["rearm_sec"] // 60, DM["dp"]))
                                dm_logged = True
                        else:
                            dm_armed_ts = None
                except Exception as e:
                    if now() - dm_err_log > 1800:
                        log("⚠ 데드맨 설정 실패(DP%s): %s" % (DM["dp"], e)); dm_err_log = now()

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
                expecting = status_ok and (not ctl_stale) and (t_ctl is not None) and (t_ctl < on_th) and cur_relay
                if expecting and cur_watt < wmin:
                    fault_dt += dt
                    if fault_dt >= grace and not fault_active:
                        fault_active = True
                        log("⚠ 히터 가열 안됨: 켜짐인데 %.0fW (%s %.1f°C<%s). 히터 자체 스위치/다이얼/콘센트 확인" % (cur_watt, ctl_sensor, t_ctl, on_th))
                else:
                    if fault_active and status_ok and cur_watt >= wmin: log("히터 정상 복귀(%.0fW)" % cur_watt)
                    if not (expecting and cur_watt < wmin): fault_dt = 0.0; fault_active = False
                # 스냅샷(heater_status.json — 서비스만 기록)
                save_atomic(STATUS, {
                    "updated": stamp(), "mode": st.get("mode"), "relay": cur_relay,
                    "watt": round(cur_watt, 0), "volt": round(cur_volt, 1),
                    "hw_kwh": cur_kwh_hw, "today_kwh": round(day_wh / 1000.0, 3), "total_kwh": round(total_wh / 1000.0, 2),
                    "heater_ok": (not fault_active), "status_fail_streak": fail_streak,
                    "day_wh": round(day_wh, 1), "total_wh": round(total_wh, 1), "day_date": day_date,
                    "ctl_sensor": ctl_sensor, "ctl_src": ctl_src, "t_ctl": t_ctl,
                    "sensor_ok": {floor_s: (not stale), **({attic_s: at_ok} if attic_s else {})},
                    "delta_attic_floor": round(delta, 2),
                    "deadman_armed": bool(dm_armed_ts), "countdown_s": cur_cd})
                thermal_log(st.get("mode"), cur_relay, cur_watt, cur_volt, t_ctl, delta,
                            floor_s, attic_s, cfg["preheat"].get("outdoor_sensor", ""))
            except Exception as e:
                log("계측 오류: %s" % e)
        except Exception as e:
            log("루프 오류: %s" % e)
        time.sleep(poll)

if __name__ == "__main__":
    main()
