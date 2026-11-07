#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SI 2노드 플랜트(센서 주변 공기 C_a ↔ 축열체 C_m) + 히터·센서 지연.
보정: UA 합 47 W/K, C 합 1.55 MJ/K(heater_config preheat: tau 9.16h), P 1770 W.
목표(11-07 저녁 실측, 외기 4°C, 12분 센서, 23/23.5): 사이클 ~1.2/h, ON ~55%, 1층 스윙 ~1.5°C, 냉간 승온 ~2.5°C/h."""
import sys, statistics, itertools
sys.stdout.reconfigure(encoding="utf-8")

P = 1770.0
ON_TH, OFF_TH = 23.0, 23.5
DT = 10.0  # s

def simulate(sens_int_min, tout, C_a, G_am, C_m=None, UA_a=15.0, UA_m=32.0, tau_heat=90.0, tau_sens=300.0,
             mode="hyst", lead_min=0.0, cap=0.5, min_on=0.0, min_off=0.0, q=0.1, hours=14, warm_h=3):
    if C_m is None: C_m = 1.55e6 - C_a
    Ta = Tm = 22.5; Ts = Ta; qh = 0.0; relay = True
    last_sw = -1e9; last_samp = -1e9; sensor = None; hist = []; series = []
    t = 0.0; T_end = hours*3600
    while t < T_end:
        if t - last_samp >= sens_int_min*60:
            v = round(Ts/q)*q; sensor = v; last_samp = t
            hist.append((t, v)); hist = hist[-8:]
        if sensor is not None:
            teff = sensor
            if mode == "pred" and len(hist) >= 2:
                pts = [(a, b) for a, b in hist if t - a <= 300+1e-6] or hist[-2:]
                if len(pts) >= 2 and pts[-1][0] > pts[0][0]:
                    n = len(pts); mx = sum(a for a, _ in pts)/n; my = sum(b for _, b in pts)/n
                    sxx = sum((a-mx)**2 for a, _ in pts)
                    r = sum((a-mx)*(b-my) for a, b in pts)/sxx if sxx > 0 else 0.0   # °C/s
                    teff = sensor + max(-cap, min(cap, r*lead_min*60))
            want = True if teff <= ON_TH else (False if teff >= OFF_TH else relay)
            dwell = (min_on if relay else min_off)*60
            if want != relay and (t - last_sw) >= dwell:
                relay = want; last_sw = t
        qh += ((1.0 if relay else 0.0) - qh)/tau_heat*DT
        dTa = (P*qh - G_am*(Ta-Tm) - UA_a*(Ta-tout))/C_a
        dTm = (G_am*(Ta-Tm) - UA_m*(Tm-tout))/C_m
        Ta += dTa*DT; Tm += dTm*DT
        Ts += (Ta-Ts)/tau_sens*DT
        series.append((t, Ta, relay)); t += DT
    s = [x for x in series if x[0] >= warm_h*3600]
    Tair = [x[1] for x in s]; span_h = (hours-warm_h)
    sw = sum(1 for i in range(1, len(s)) if s[i][2] != s[i-1][2])
    on = sum(DT for x in s if x[2])/(span_h*3600)
    target = (ON_TH+OFF_TH)/2
    return dict(cyc=(sw/2)/span_h, swing=max(Tair)-min(Tair), over=max(Tair)-OFF_TH,
                under=ON_TH-min(Tair), on=on, dev=statistics.mean(abs(x-target) for x in Tair), kwh=on*P/1000*24,
                tmean=statistics.mean(Tair))

def cold_rise(C_a, G_am, tout=0.7, UA_a=15.0, UA_m=32.0):
    """18°C 균일 상태에서 ON 유지 2시간 → 센서 온도 상승률(°C/h, 첫 2시간 평균)"""
    Ta = Tm = 18.0; Ts = 18.0; C_m = 1.55e6 - C_a; t = 0.0
    while t < 7200:
        dTa = (P - G_am*(Ta-Tm) - UA_a*(Ta-tout))/C_a
        dTm = (G_am*(Ta-Tm) - UA_m*(Tm-tout))/C_m
        Ta += dTa*DT; Tm += dTm*DT; Ts += (Ta-Ts)/300*DT; t += DT
    return (Ts-18.0)/2

if __name__ == "__main__":
    print("== 보정 탐색(외기 4°C, 12분 센서): 목표 사이클 1.2/h, ON 55%, 스윙 1.5, 냉간 승온 2.5°C/h ==")
    best = None
    for C_a, G_am in itertools.product([0.1e6, 0.15e6, 0.2e6, 0.3e6, 0.4e6], [150, 250, 400, 600, 900]):
        r = simulate(12, 4.0, C_a, G_am); cr = cold_rise(C_a, G_am, 4.0)
        err = ((r["cyc"]-1.2)/1.2)**2 + ((r["on"]-0.55)/0.55)**2 + ((r["swing"]-1.5)/1.5)**2 + ((cr-2.5)/2.5)**2
        if best is None or err < best[0]: best = (err, C_a, G_am, r, cr)
        print("C_a %.2fMJ/K G_am %4d: 사이클 %.2f/h ON %.0f%% 스윙 %.2f 승온 %.2f°C/h  err %.2f"
              % (C_a/1e6, G_am, r["cyc"], r["on"]*100, r["swing"], cr, err))
    _, C_a, G_am, r, cr = best
    print("\n선택: C_a %.2f MJ/K, G_am %d W/K (공기 시정수 %.1f분)\n" % (C_a/1e6, G_am, C_a/G_am/60))

    for tout in (0.7, 4.0):
        print("== 외기 %.1f°C ==" % tout)
        print("%-38s %7s %6s %6s %6s %5s %7s %7s" % ("케이스", "사이클/h", "스윙", "오버", "언더", "ON%", "평균편차", "kWh/일"))
        for name, kw in [
            ("순정 12분, 히스테리시스(기준)",        dict(sens_int_min=12, mode="hyst")),
            ("1분, 히스테리시스만",                  dict(sens_int_min=1,  mode="hyst")),
            ("1분 + 예측 lead 3분",                  dict(sens_int_min=1,  mode="pred", lead_min=3)),
            ("1분 + 예측 lead 5분",                  dict(sens_int_min=1,  mode="pred", lead_min=5)),
            ("1분 + 예측 lead 8분",                  dict(sens_int_min=1,  mode="pred", lead_min=8)),
            ("1분 히스테리시스 + 최소ON/OFF 5분",     dict(sens_int_min=1,  mode="hyst", min_on=5, min_off=5)),
            ("1분 + 예측 5분 + 최소ON/OFF 5분",       dict(sens_int_min=1,  mode="pred", lead_min=5, min_on=5, min_off=5)),
        ]:
            r = simulate(tout=tout, C_a=C_a, G_am=G_am, **kw)
            print("%-38s %7.1f %6.2f %+6.2f %+6.2f %5.0f %7.2f %7.1f" %
                  (name, r["cyc"], r["swing"], r["over"], r["under"], r["on"]*100, r["dev"], r["kwh"]))
        print()
