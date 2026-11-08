# -*- coding: utf-8 -*-
"""예열 실험 시뮬: 일사(F)·풍속(E)이 예열에 미치는 영향.
   1차 열모델: C·dT/dt = P_heater − UA_eff·(T_in−T_out) + Q_solar
     UA_eff = UA·(1 + k_wind·WSD)        (E: 바람이 손실 키움)
     Q_solar = S_peak·sky_factor·주간곡선  (F: 맑으면 낮에 공짜 열)
   파라미터: UA=47 W/K, τ=9.16h → C=UA·τ. P=1740W(100%). (현 config 값)
   일사 보정: 2026-11-06 맑은 날 frost(히터off) 실측으로 S_peak_clear≈1300W 보정
     (그날 낮 외기~12°C에 실내 25°C → Q/UA≈13 → ~600W, 겨울 여유로 1300).
   면책: 상대비교용 실험. 절대값은 보정에 민감."""
import math

UA = 47.0
TAU_S = 9.16 * 3600
C_J = UA * TAU_S          # 열용량 J/K
P = 1740.0               # 히터 100%
K_WIND = 0.05            # 풍속 1 m/s당 유효 UA +5% (가정, 백테스트 대상)
DT = 600                 # 10분
T_START = 5.0            # 동파 밴드 상단에서 시작
TARGET = 23.0
ARRIVE_H = 20.0

def t_out(h, tmin, tmax):      # 최저 06시, 최고 15시
    mid = (tmax + tmin) / 2; amp = (tmax - tmin) / 2
    return mid - amp * math.cos(2 * math.pi * (h - 6) / 18)

def q_solar(h, s_peak):        # 주간(07~17) 반sin, 피크 12시
    if 7 <= h <= 17:
        return s_peak * max(0.0, math.sin(math.pi * (h - 7) / 10))
    return 0.0

def run(tmin, tmax, s_peak, wind, heater=True):
    ua = UA * (1 + K_WIND * wind)
    T = T_START; h = 0.0; kwh = 0.0; cross = None; peak = T
    while h < ARRIVE_H:
        to = t_out(h, tmin, tmax); qs = q_solar(h, s_peak)
        on = heater and (T < TARGET)
        pin = P if on else 0.0
        dT = (pin - ua * (T - to) + qs) * DT / C_J
        if on: kwh += P * DT / 3600 / 1000
        T += dT; h += DT / 3600
        peak = max(peak, T)
        if cross is None and T >= TARGET: cross = h
    return {"T20": T, "cross": cross, "kwh": kwh, "peak": peak}

print("=== F(일사)·E(풍속) 예열 실험 — 겨울 추운 날, 00시 동파5°C 시작→20시 도착 23°C 목표 ===")
print("모델: UA=47, τ=9.16h, 히터 100%(1740W), k_wind=0.05, 일사피크 맑음1300/구름650/흐림260W\n")

print("[A] 일사만의 힘 (히터 OFF, 낮 일사로 실내가 어디까지?)")
for name, tmin, tmax, sp in [("맑음(야간복사냉각↓ 최저-10)", -10, 0, 1300),
                             ("흐림(야간 온화 최저-7)", -7, 1, 260)]:
    r = run(tmin, tmax, sp, 0, heater=False)
    print("  %-26s 낮 실내피크 %.1f°C (20시 %.1f°C)" % (name, r["peak"], r["T20"]))

print("\n[B] 예열(히터 100%, 00시부터) — 하늘×바람 2×2")
rows = [("맑음·무풍", -10, 0, 1300, 0), ("맑음·바람6m/s", -10, 0, 1300, 6),
        ("흐림·무풍", -7, 1, 260, 0), ("흐림·바람6m/s", -7, 1, 260, 6)]
print("  %-14s %-10s %-12s %-8s" % ("시나리오", "23°C도달", "20시실내", "전기(kWh)"))
for name, tmin, tmax, sp, w in rows:
    r = run(tmin, tmax, sp, w, heater=True)
    cross = ("%.1fh" % r["cross"]) if r["cross"] else "미도달"
    print("  %-14s %-10s %-12s %.1f" % (name, cross, "%.1f°C" % r["T20"], r["kwh"]))

print("\n[C] 참고: 같은 맑은 날, 예열 없이 '낮 피크부터 시작'하면? (13시 시작, 그전 일사로 데워진 상태)")
# 13시 일사피크 때 free-run으로 데워진 T를 시작점으로 13→20시만 예열
def run_from(start_h, T0, tmin, tmax, s_peak, wind):
    ua = UA*(1+K_WIND*wind); T=T0; h=start_h; kwh=0.0; cross=None
    while h < ARRIVE_H:
        to=t_out(h,tmin,tmax); qs=q_solar(h,s_peak); on=T<TARGET; pin=P if on else 0
        dT=(pin-ua*(T-to)+qs)*DT/C_J
        if on: kwh+=P*DT/3600/1000
        T+=dT; h+=DT/3600
        if cross is None and T>=TARGET: cross=h
    return T,cross,kwh
# 맑은날 free-run으로 13시 실내온도 구하기
free=run(-10,0,1300,0,heater=False)
# 13시 시점 T를 다시 구하려 free-run 재현
Tt=T_START; hh=0
while hh<13:
    to=t_out(hh,-10,0); qs=q_solar(hh,1300)
    Tt+=(-UA*(Tt-to)+qs)*DT/C_J; hh+=DT/3600
T13=Tt
T20,cr,kwh=run_from(13,T13,-10,0,1300,0)
print("  맑음·무풍: 13시 실내 %.1f°C(일사로)에서 예열 시작 → 23°C 도달 %s, 20시 %.1f°C, 전기 %.1f kWh"
      % (T13, ("%.1fh"%cr) if cr else "미도달", T20, kwh))
