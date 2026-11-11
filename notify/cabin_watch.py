#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""농막 감시 → 텔레그램: 과열 · 히터 오류 · 동파 위험(실측·예보). 설계·설정: docs/TELEGRAM.ko.md §4

히터 제어기(heater_control.py)와 음성 경보(alert_sound.py)는 건드리지 않고, 그들이 남기는 파일만 읽는다.
  heater_status.json(제어기 스냅샷) · heater_state.json(모드·임계) · latest.json(센서) · forecast.json(단기예보)
이 서비스가 죽거나 오류가 나도 히터 제어에는 영향이 없다. 음성 경보는 재실(comfort)에서만 울리지만
텔레그램은 **모든 모드**(특히 아무도 없는 동파 모드)에서 보낸다.

경보마다 상태(켜짐/꺼짐)를 저장해 '처음 켜질 때' 한 번 보내고, 계속되면 remind 간격마다 다시 알리고,
풀리면 '정상 복귀'를 보낸다(재시작해도 중복 전송 없음: cabin_watch_state.json).

실행: python3 notify/cabin_watch.py            # 1분 주기 감시(systemd cabin-watch.service)
      python3 notify/cabin_watch.py --once     # 한 번 판단하고 보낼 내용만 출력(전송 안 함)
설정: heater_config.json 의 "notify" 키로 기본값(아래 DEFAULTS)을 덮어쓸 수 있다(즉시 반영).
"""
import os, sys, json, time, datetime

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import telegram_notify as tg   # noqa: E402

BASE = os.path.expanduser(os.environ.get("SMART_CABIN_DIR", "~/smart-cabin"))
P = {k: os.path.join(BASE, v) for k, v in {
    "cfg": "heater_config.json", "state": "heater_state.json", "status": "heater_status.json",
    "latest": "latest.json", "forecast": "forecast.json", "watch": "cabin_watch_state.json",
    "log": "cabin_watch.log"}.items()}

DEFAULTS = {
    "enabled": True,
    # 과열: 센서별 상한(°C). 모든 모드. 음성은 26°C '문 여세요'(재실)이고 이건 '이상' 수준.
    "overheat_c": {"내부1층": 28.0, "다락": 32.0},
    # 동파 모드인데 히터가 안 꺼짐: 제어온도 ≥ frost.off + 이 값이고 히터가 전력을 먹는 중
    "frost_stuck_margin_c": 4.0,
    # 실내 저온(동파 임박): 1층(또는 제어온도) ≤ frost.on − 이 값
    "low_indoor_margin_c": 1.0,
    # 제어기 멈춤: heater_status.json 이 이보다 오래되면(분)
    "status_stale_min": 10,
    # 플러그 통신: 상태조회 연속 실패 횟수
    "plug_fail_streak": 5,
    # 동파 위험 예보: 앞으로 forecast_hours 시간 예보 최저가 이하이면 '주의'
    "forecast_hours": 48,
    "forecast_frost_c": -5.0,
    # 예보 + 지금 히터 단(W)로 계산한 실내 도달 온도가 frost.on + 이 값 미만이면 '위험'(히터 단 올리기)
    "forecast_margin_c": 2.0,
    "forecast_max_age_h": 12,          # 이보다 오래된 forecast.json은 쓰지 않음
    # 다시 알리는 간격(분)
    "remind_min": {"overheat": 30, "frost_stuck": 60, "low_indoor": 60, "heater_fault": 180,
                   "plug_comm": 180, "controller_down": 180, "sensor_lost": 360, "forecast_frost": 720},
}
ALARMS = ("overheat", "frost_stuck", "low_indoor", "heater_fault", "plug_comm",
          "controller_down", "sensor_lost", "forecast_frost")


def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as f: return json.load(f)
    except Exception:
        return default


def log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(P["log"], "a", encoding="utf-8") as f: f.write(line + "\n")
    except Exception:
        pass


def ncfg(cfg):
    out = json.loads(json.dumps(DEFAULTS))
    user = (cfg or {}).get("notify") or {}
    for k, v in user.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict): out[k].update(v)
        else: out[k] = v
    return out


def _age_min(updated, now):
    try:
        return (now - datetime.datetime.strptime(updated, "%Y-%m-%d %H:%M:%S").timestamp()) / 60.0
    except Exception:
        return None


def _sensor(latest, name, now, stale_min):
    """→ (온도 또는 None, 신선한가)."""
    r = (latest or {}).get(name) or {}
    t, ms = r.get("temp"), r.get("time_ms")
    if t is None or ms is None: return None, False
    return float(t), (now - ms / 1000.0) <= stale_min * 60


def _f1(x): return "%.1f" % x


MODE_KR = {"frost": "동파", "comfort": "난방", "sleep": "취침"}


def forecast_min(fc, now, hours):
    """→ (최저 기온, 그 시각 'MM/DD HH시') 또는 (None, None)."""
    best = None
    for h in (fc or {}).get("hourly") or []:
        ms, t = h.get("ms"), h.get("tmp")
        if ms is None or t is None: continue
        if now * 1000 - 3600e3 <= ms <= now * 1000 + hours * 3600e3 and (best is None or t < best[0]):
            best = (t, ms)
    if not best: return None, None
    return best[0], time.strftime("%m/%d %H시", time.localtime(best[1] / 1000))


def evaluate(now, cfg, state, status, latest, fc, fc_mtime=None):
    """순수 판단 → {경보키: (켜짐?, 켜짐 문구, 복귀 문구)}. 판단할 자료가 없으면 그 키는 빠진다(상태 유지)."""
    cfg = cfg or {}; N = ncfg(cfg); state = state or {}
    out = {}
    stale_min = cfg.get("stale_temp_min", 45)
    frost = cfg.get("frost", {"on": 3, "off": 5})
    mode = (status or {}).get("mode") or state.get("mode")
    mode_kr = MODE_KR.get(mode, mode or "?")

    # ── 제어기 살아 있나 ──
    st_age = _age_min((status or {}).get("updated"), now) if status else None
    alive = st_age is not None and st_age <= N["status_stale_min"]
    out["controller_down"] = (
        not alive,
        "🛑 히터 제어기 응답 없음: 상태 기록이 %s 멈춤. Pi에서 `systemctl status heater-control` 확인"
        % ("%.0f분째" % st_age if st_age is not None else "없음/읽기 실패로"),
        "✅ 히터 제어기 정상 복귀")

    # ── 센서 ──
    temps = {}
    for name in sorted(set(list(N["overheat_c"]) + [cfg.get("sensor", "내부1층")])):
        t, fresh = _sensor(latest, name, now, stale_min)
        if fresh: temps[name] = t
    floor = cfg.get("sensor", "내부1층")
    lost = [n for n in N["overheat_c"] if n not in temps]
    if latest is not None:
        out["sensor_lost"] = (bool(lost),
                              "📡 실내 센서 수신 끊김(%s): %d분 넘게 새 값 없음. 게이트웨이·센서 배터리 확인" % (", ".join(lost), stale_min),
                              "✅ 실내 센서 수신 복귀")

    # ── 과열(모든 모드) ──
    hot = [(n, t, N["overheat_c"][n]) for n, t in temps.items() if n in N["overheat_c"] and t >= N["overheat_c"][n]]
    if temps:
        out["overheat"] = (bool(hot),
                           "🔥 과열: " + ", ".join("%s %s°C(기준 %s)" % (n, _f1(t), _f1(th)) for n, t, th in hot)
                           + " · 모드 %s · 히터 %s" % (mode_kr, _heater_txt(status)),
                           "✅ 과열 해소: " + ", ".join("%s %s°C" % (n, _f1(t)) for n, t in sorted(temps.items())))

    if alive:
        t_ctl = status.get("t_ctl"); watt = status.get("watt") or 0
        # ── 동파 모드인데 히터가 안 꺼짐(플러그가 명령을 안 듣거나 제어 이상) ──
        if mode == "frost" and t_ctl is not None:
            lim = frost["off"] + N["frost_stuck_margin_c"]
            out["frost_stuck"] = (t_ctl >= lim and watt >= 100,
                                  "🔥 동파 모드인데 히터가 꺼지지 않음: 제어온도 %s°C(꺼짐 기준 %s°C), %.0fW 사용 중. 플러그 앱으로 끄고 확인"
                                  % (_f1(t_ctl), _f1(frost["off"]), watt),
                                  "✅ 동파 모드 히터 정상(꺼짐)")
        # ── 히터 가열 안 됨(켜졌는데 전력 없음: 히터 자체 스위치·다이얼·콘센트) ──
        out["heater_fault"] = (status.get("heater_ok") is False,
                               "⚠ 히터 가열 안 됨: 플러그는 켜졌는데 %.0fW. 히터 본체 스위치·다이얼·콘센트 확인 (모드 %s, 제어온도 %s°C)"
                               % (watt, mode_kr, "?" if t_ctl is None else _f1(t_ctl)),
                               "✅ 히터 가열 정상 복귀(%.0fW)" % watt)
        # ── 플러그 통신 ──
        fs = status.get("status_fail_streak", 0) or 0
        out["plug_comm"] = (fs >= N["plug_fail_streak"],
                            "⚠ 히터 플러그 통신 끊김: %d회 연속 조회 실패. 플러그 전원·와이파이(공유기) 확인. "
                            "데드맨이 설정돼 있으면 30분 뒤 플러그가 스스로 켜짐" % fs,
                            "✅ 히터 플러그 통신 복귀")

    # ── 실내 저온(동파 임박, 실측) ──
    t_low = temps.get(floor)
    if t_low is None and alive and status.get("t_ctl") is not None and mode == "frost":
        t_low = status.get("t_ctl")
    if t_low is not None:
        lim = frost["on"] - N["low_indoor_margin_c"]
        out["low_indoor"] = (t_low <= lim,
                             "🥶 실내 저온: %s %s°C (동파 켜짐 기준 %s°C보다 낮음) · 히터 %s · 모드 %s"
                             % (floor, _f1(t_low), _f1(frost["on"]), _heater_txt(status), mode_kr),
                             "✅ 실내 온도 회복: %s %s°C" % (floor, _f1(t_low)))

    # ── 동파 위험 예보 ──
    fc_ok = fc is not None and (fc_mtime is None or now - fc_mtime <= N["forecast_max_age_h"] * 3600)
    if fc_ok:
        tmin, when = forecast_min(fc, now, N["forecast_hours"])
        if tmin is not None:
            pw = (status or {}).get("stage_w")
            ua = (cfg.get("preheat") or {}).get("ua_w_per_k")
            t_eq = (tmin + pw / ua) if (pw and ua) else None
            danger = t_eq is not None and t_eq < frost["on"] + N["forecast_margin_c"]
            active = tmin <= N["forecast_frost_c"] or danger
            msg = "❄ 동파 %s: %d시간 예보 최저 %s°C (%s)" % ("위험" if danger else "주의", N["forecast_hours"], _f1(tmin), when)
            if t_eq is not None:
                stage = (status or {}).get("stage")
                msg += " · 지금 히터 %s단 %.0fW면 실내 최저 약 %s°C" % (stage or "?", pw, _f1(t_eq))
                if danger: msg += " → 히터 단을 올리세요(동파 켜짐 기준 %s°C)" % _f1(frost["on"])
            msg += " · 모드 %s" % mode_kr
            if mode != "frost": msg += " (떠날 때 동파 모드 확인)"
            out["forecast_frost"] = (active, msg, "✅ 동파 위험 예보 해제: %d시간 예보 최저 %s°C" % (N["forecast_hours"], _f1(tmin)))
    return out


def _heater_txt(status):
    if not status: return "상태 미상"
    if not status.get("relay"): return "꺼짐"
    w = status.get("watt") or 0
    return "켜짐 %.0fW%s" % (w, " (%s단)" % status["stage"] if status.get("stage") else "")


def step(now, decisions, wstate, N, send):
    """경보 전이 처리 → 보낸 문구 목록. wstate: {키: {"on": bool, "last": ts}} (제자리 갱신)."""
    sent = []
    for key in ALARMS:
        if key not in decisions: continue
        active, msg, rec = decisions[key]
        s = wstate.setdefault(key, {"on": False, "last": 0})
        remind = N["remind_min"].get(key, 180) * 60
        if active and (not s["on"] or now - s["last"] >= remind):
            prefix = "" if not s["on"] else "(계속) "
            send(prefix + msg); sent.append(prefix + msg)
            s["on"] = True; s["last"] = now
        elif not active and s["on"]:
            send(rec); sent.append(rec)
            s["on"] = False; s["last"] = now
    return sent


def gather():
    fc_mtime = None
    try: fc_mtime = os.path.getmtime(P["forecast"])
    except Exception: pass
    return (load(P["cfg"], {}), load(P["state"], {}), load(P["status"]),
            load(P["latest"]), load(P["forecast"]), fc_mtime)


def save_state(ws):
    tmp = P["watch"] + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f: json.dump(ws, f, ensure_ascii=False)
    os.replace(tmp, P["watch"])


def run_once(dry=False, now=None):
    now = now or time.time()
    cfg, state, status, latest, fc, fc_mtime = gather()
    N = ncfg(cfg)
    if not N.get("enabled", True): return []
    dec = evaluate(now, cfg, state, status, latest, fc, fc_mtime)
    if dry:
        for k in ALARMS:
            if k in dec: print("%-16s %s  %s" % (k, "켜짐" if dec[k][0] else "-", dec[k][1] if dec[k][0] else ""))
        return []
    ws = load(P["watch"], {}) or {}
    sent = step(now, dec, ws, N, lambda m: tg.send(m))
    if sent:
        save_state(ws)
        for m in sent: log("보냄: " + m)
    return sent


def main(argv):
    if "--once" in argv:
        run_once(dry=True); return 0
    log("감시 시작")
    while True:
        try:
            run_once()
        except Exception as e:
            log("감시 오류: %s" % e)
        time.sleep(60)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
