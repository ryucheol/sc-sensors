#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""실내 예측 기록장 — 2노드 모델의 '밤사이 예측'을 남기고 다음 날 실측과 맞춰 본다.

  predict  (매일 18:05) 지금 1층 공기·가구 온도(heater_status.json t_mass)와 시간별 예보(forecast.json)로
           내일 07:00 1층 온도, 밤사이(지금~09:00) 최저와 그 시각, 동파 히터가 켜질지·켜면 몇 kWh를
           2노드 모델(heater_control.PREHEAT2_DEFAULT/two_node)로 계산해 predict_log.jsonl 에 한 줄 추가.
           그때 도착 예약 예고(arrival_start_pred·arrival_solar_start)도 같이 남긴다.
  verify   (매일 09:10) 아직 실측이 없고 대상 시각이 지난 줄에 thermal_log.csv(1분 기록)의 실측
           (07:00 1층, 최저·시각, 히터 kWh·가동 여부)과 오차를 채운다.
  push     기록을 시트 '<SITE>_실내예측검증' 탭으로 보낸다(Code.gs v17 mode:pred, 대상 날짜 키로 덮어씀).
  (인자 없음) verify → predict(18시 이후면) → push

모델 가정: 바깥 = 예보 기온 + OUTDOOR_OFFSET(바닥외부 센서가 관측소보다 밤에 높음: 11-08~22 평균 +0.1~0.5, 11-24 밤 +1.0),
          해는 07:00 전이라 넣지 않음, 동파 모드 히스테리시스(frost.on/off)대로 메인 히터(마지막 단 W).
실행(cron):  5 18 * * *  cd ~/smart-cabin && python3 heater/predict_log.py predict push >> predict_log.out 2>&1
            10 9 * * *  cd ~/smart-cabin && python3 heater/predict_log.py verify push >> predict_log.out 2>&1
시험:        python3 heater/predict_log.py predict --dry-run
"""
import os, sys, json, csv, time, datetime as dt

HERE = os.path.dirname(os.path.realpath(__file__))
BASE = os.path.dirname(HERE)
sys.path.insert(0, HERE)
LOG = os.path.join(BASE, "predict_log.jsonl")
THERMAL = os.path.join(BASE, "thermal_log.csv")
STATUS = os.path.join(BASE, "heater_status.json")
LATEST = os.path.join(BASE, "latest.json")
CFG = os.path.join(BASE, "heater_config.json")

OUTDOOR_OFFSET = 0.8     # 바닥외부(모델 입력) − 관측소 예보, 밤 평균. 11-08~22 흐린 밤 +0.1~맑은 밤 +0.5, 11-24 밤 +1.0 → 기록으로 다듬기
TARGET_HOUR = 7          # 예측 대상 시각(해 뜨기 전)
WINDOW_END_HOUR = 8      # 최저 찾는 구간 끝(일출 07:20 무렵 — 그 뒤는 해가 데움, 모델엔 해 없음)


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def simulate_night(p2, t_air, t_mass, t_out_at, t0, t1, frost_on, frost_off, p_heat, step=60):
    """2노드 자유 냉각 + 동파 히스테리시스 시뮬레이션(순수).
       t_out_at(ts) → 바깥 °C. 반환 dict: traj [(ts, 공기)], min_t, min_ts, heater_on(bool), heater_kwh."""
    ca = p2["ca_kwh"] * 3.6e6; cm = p2["cm_kwh"] * 3.6e6
    ta, tm, ts = float(t_air), float(t_mass if t_mass is not None else t_air), float(t0)
    on = False; kwh = 0.0; ever = False
    traj = []; mn, mn_ts = ta, ts
    while ts < t1:
        to = t_out_at(ts)
        if ta <= frost_on: on = True
        elif ta >= frost_off: on = False
        P = p_heat if on else 0.0
        ever = ever or on
        dta = (P - p2["h_am"] * (ta - tm) - p2["h_ao"] * (ta - to)) / ca
        dtm = (p2["h_am"] * (ta - tm) - p2["h_mo"] * (tm - to)) / cm
        ta += dta * step; tm += dtm * step; ts += step
        kwh += P * step / 3.6e6
        if ta < mn: mn, mn_ts = ta, ts
        if int(ts) % 600 == 0: traj.append((ts, round(ta, 2)))
    return {"traj": traj, "min_t": round(mn, 2), "min_ts": mn_ts, "heater_on": ever, "heater_kwh": round(kwh, 2),
            "end_t": round(ta, 2)}


def series_fn(series, fallback):
    """[(ts, °C)] → 가장 가까운 시각 값(+OUTDOOR_OFFSET). 범위 밖은 끝값, 없으면 fallback."""
    def f(ts):
        if not series:
            return fallback
        if ts <= series[0][0]: return series[0][1] + OUTDOOR_OFFSET
        if ts >= series[-1][0]: return series[-1][1] + OUTDOOR_OFFSET
        lo = max(i for i, (t, _) in enumerate(series) if t <= ts)
        (a, va), (b, vb) = series[lo], series[min(lo + 1, len(series) - 1)]
        v = va if b == a else va + (vb - va) * (ts - a) / (b - a)
        return v + OUTDOOR_OFFSET
    return f


def target_times(now):
    """지금 기준 다음 07:00 과 09:00 (datetime)."""
    tgt = now.replace(hour=TARGET_HOUR, minute=0, second=0, microsecond=0)
    if tgt <= now: tgt += dt.timedelta(days=1)
    return tgt, tgt.replace(hour=WINDOW_END_HOUR)


def make_prediction(now=None):
    import heater_control as hc
    now = now or dt.datetime.now()
    cfg = load(CFG, {}); p2 = hc.p2_cfg(cfg)
    st = load(STATUS, {}); lat = load(LATEST, {})
    floor_s = cfg.get("sensor", "내부1층"); out_s = (cfg.get("preheat") or {}).get("outdoor_sensor", "바닥외부")
    attic_s = (cfg.get("sleep") or {}).get("sensor", "다락")
    t1 = (lat.get(floor_s) or {}).get("temp"); t2 = (lat.get(attic_s) or {}).get("temp"); to = (lat.get(out_s) or {}).get("temp")
    if t1 is None:
        return None
    tm = st.get("t_mass", t1)
    series = hc.forecast_series()
    tgt, wend = target_times(now)
    fr = cfg.get("frost", {"on": 3, "off": 5})
    p_heat = st.get("stage_w") or cfg["preheat"]["heater_watts"]
    t0 = now.timestamp()
    sim_t = simulate_night(p2, t1, tm, series_fn(series, to if to is not None else 0.0), t0, tgt.timestamp(),
                           fr["on"], fr["off"], p_heat)
    sim_w = simulate_night(p2, t1, tm, series_fn(series, to if to is not None else 0.0), t0, wend.timestamp(),
                           fr["on"], fr["off"], p_heat)
    # 히터 없이 식으면 몇 도까지(동파 하한 3°C에 잘리지 않은 값 — 실측 최저와 비교해 모델 오차를 보기 좋음)
    sim_f = simulate_night(p2, t1, tm, series_fn(series, to if to is not None else 0.0), t0, wend.timestamp(),
                           -99, -98, 0.0)
    fc = [v for ts, v in series if t0 <= ts <= wend.timestamp()]
    return {"target": tgt.strftime("%Y-%m-%d %H:%M"), "made": now.strftime("%Y-%m-%d %H:%M"),
            "model": "2node", "t1_now": t1, "t2_now": t2, "to_now": to, "tmass_now": round(tm, 2) if tm is not None else None,
            "fc_min": min(fc) if fc else None,
            "pred_t1_target": sim_t["end_t"], "pred_min_t1": sim_w["min_t"], "pred_free_min": sim_f["min_t"],
            "pred_free_min_at": dt.datetime.fromtimestamp(sim_f["min_ts"]).strftime("%H:%M"),
            "pred_min_at": dt.datetime.fromtimestamp(sim_w["min_ts"]).strftime("%H:%M"),
            "pred_heater_on": sim_w["heater_on"], "pred_heater_kwh": sim_w["heater_kwh"],
            "arrival_start_pred": _fmt_ts(st.get("arrival_start_pred")),
            "arrival_solar_start": _fmt_ts(st.get("arrival_solar_start")),
            "arrival": _fmt_ts((st.get("arrival") or {}).get("ts") if isinstance(st.get("arrival"), dict) else None)}


def _fmt_ts(ts):
    try:
        return dt.datetime.fromtimestamp(float(ts)).strftime("%m-%d %H:%M") if ts else None
    except (TypeError, ValueError):
        return None


def read_thermal(t_from, t_to, path=THERMAL):
    """thermal_log.csv 중 [t_from, t_to] 줄 → [(datetime, t1, relay, watt)]."""
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                try:
                    t = dt.datetime.strptime(r["ts"], "%Y-%m-%d %H:%M:%S")
                except (KeyError, ValueError):
                    continue
                if t < t_from or t > t_to: continue
                try:
                    v = float(r.get("t1") or "nan")
                except ValueError:
                    v = float("nan")
                out.append((t, v, r.get("relay") == "1", float(r.get("watt") or 0)))
    except FileNotFoundError:
        pass
    return out


def actuals(rows, target):
    """1분 기록 → 실측(순수): 대상 시각 1층, 최저·시각, 히터 kWh(간격 최대 5분), 가동 여부."""
    good = [(t, v, rel, w) for t, v, rel, w in rows if v == v]
    if not good:
        return None
    at = min(good, key=lambda x: abs((x[0] - target).total_seconds()))
    mn = min(good, key=lambda x: x[1])
    kwh = 0.0
    for (ta, _, _, wa), (tb, _, _, _) in zip(rows, rows[1:]):
        kwh += wa * min((tb - ta).total_seconds(), 300) / 3.6e6
    return {"act_t1_target": round(at[1], 2), "act_min_t1": round(mn[1], 2), "act_min_at": mn[0].strftime("%H:%M"),
            "act_heater_on": any(rel for _, _, rel, _ in rows), "act_heater_kwh": round(kwh, 2)}


def verify(now=None, log_path=LOG, thermal_path=THERMAL):
    now = now or dt.datetime.now()
    entries = read_log(log_path); changed = 0
    for e in entries:
        if "act_t1_target" in e: continue
        tgt = dt.datetime.strptime(e["target"], "%Y-%m-%d %H:%M")
        wend = tgt.replace(hour=WINDOW_END_HOUR)
        if now < wend: continue
        made = dt.datetime.strptime(e["made"], "%Y-%m-%d %H:%M")
        a = actuals(read_thermal(made, wend, thermal_path), tgt)
        if not a: continue
        e.update(a)
        e["err_t1_target"] = round(e["pred_t1_target"] - a["act_t1_target"], 2)
        e["err_min_t1"] = round(e["pred_min_t1"] - a["act_min_t1"], 2)
        if e.get("pred_free_min") is not None and not a["act_heater_on"]:   # 히터가 안 돌았으면 자유 냉각 예측과 비교
            e["err_free_min"] = round(e["pred_free_min"] - a["act_min_t1"], 2)
        changed += 1
    if changed: write_log(entries, log_path)
    return changed


def read_log(path=LOG):
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if ln:
                    try: out.append(json.loads(ln))
                    except ValueError: pass
    except FileNotFoundError:
        pass
    return out


def write_log(entries, path=LOG):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for e in entries: f.write(json.dumps(e, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


SHEET_COLS = ["target", "made", "t1_now", "tmass_now", "to_now", "fc_min", "pred_t1_target", "act_t1_target", "err_t1_target",
              "pred_free_min", "pred_free_min_at", "act_min_t1", "act_min_at", "err_free_min",
              "pred_heater_on", "act_heater_on", "pred_heater_kwh", "act_heater_kwh", "arrival", "arrival_start_pred", "arrival_solar_start"]


def sheet_rows(entries, last_n=60):
    def cell(v):
        if isinstance(v, bool): return "켜짐" if v else "꺼짐"
        return "" if v is None else v
    return [[cell(e.get(c)) for c in SHEET_COLS] for e in entries[-last_n:]]


def push(entries):
    import sheet_cmd_poll as scp
    url, secret = scp.load_env()
    res = scp.post(url, {"secret": secret, "mode": "pred", "rows": sheet_rows(entries)})
    if not res.get("ok") or res.get("mode") != "pred":
        # v16 이하 웹앱은 mode:pred를 몰라 기본 경로로 빠져 {ok:true, added:0, note:'empty'}를 돌려준다
        print("시트 전송 실패 — 웹앱이 v17이 아님(배포 관리 → 기존 웹앱 편집 → 새 버전):", res)
        return False
    print("시트 전송:", {k: res.get(k) for k in ("added", "updated")})
    return True


def main(argv):
    dry = "--dry-run" in argv
    acts = [a for a in argv if not a.startswith("--")] or ["verify"] + (["predict"] if dt.datetime.now().hour >= 18 else []) + ["push"]
    if "verify" in acts:
        n = verify() if not dry else 0
        print("검증 채움: %d줄" % n)
    if "predict" in acts:
        p = make_prediction()
        if p is None:
            print("예측 생략: 1층 온도 없음")
        else:
            print("예측:", json.dumps(p, ensure_ascii=False))
            if not dry:
                entries = [e for e in read_log() if e.get("target") != p["target"]] + [p]   # 같은 대상은 새 예측으로
                write_log(entries)
    if "push" in acts and not dry:
        try:
            push(read_log())
        except Exception as e:
            print("시트 전송 오류(다음에 다시):", e)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
