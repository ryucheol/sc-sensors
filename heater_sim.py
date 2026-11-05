#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""히터 제어 시뮬레이터 — 센서 지연(기본 5분) 하에서 제어 방식 비교. 표준 라이브러리만 사용.

  python3 heater_sim.py                 # 기본 비교표
  python3 heater_sim.py --lag 10        # 센서 지연 10분
  python3 heater_sim.py --ua-err 0.2    # 제어기가 아는 UA가 실제와 20% 다를 때(강건성)

플랜트(실제 건물, 제어기는 모름): 공기노드(Ca) ─ 건물질량노드(Cm), 공기→외기 UA.
  heater P[W]가 공기노드로 들어감. 실효 시정수 ≈ (Ca+Cm)/UA ≈ 9h (heater_config.json의 tau_hours 9.16, UA 47).
센서: 공기온도를 lag분 지연 + 0.1°C 양자화 + 가우시안 노이즈. 제어기는 센서값만 본다.
평가는 '실제 공기온도' 기준. 근거문헌의 비대칭 기준(Favero 2021): 추운 쪽은 하강폭, 더운 쪽은 절대온도.
"""
import math, random, argparse, collections

# ── 플랜트 ─────────────────────────────────────────────
class Plant:
    def __init__(self, ua=47.0, ca=2.0e5, cm=1.35e6, ua_am=400.0, p_w=1740.0, t0=23.5):
        self.ua, self.ca, self.cm, self.ua_am, self.p = ua, ca, cm, ua_am, p_w
        self.ta = t0; self.tm = t0
    def step(self, dt, on, t_out):
        qa = (self.p if on else 0.0) - self.ua * (self.ta - t_out) - self.ua_am * (self.ta - self.tm)
        qm = self.ua_am * (self.ta - self.tm)
        self.ta += qa * dt / self.ca; self.tm += qm * dt / self.cm

class Sensor:
    """지연 + 양자화 + 노이즈. read(t, true_ta)"""
    def __init__(self, lag_min, noise=0.05, q=0.1, seed=1):
        self.lag = lag_min * 60.0; self.noise = noise; self.q = q
        self.buf = collections.deque(); self.rng = random.Random(seed)
    def read(self, t, ta):
        self.buf.append((t, ta))
        while len(self.buf) > 1 and self.buf[1][0] <= t - self.lag: self.buf.popleft()
        v = self.buf[0][1] if (t - self.lag) >= self.buf[0][0] or len(self.buf) == 1 else self.buf[0][1]
        v += self.rng.gauss(0, self.noise)
        return round(v / self.q) * self.q

def outdoor(t, mean, amp):          # 일주기: 새벽 5시 최저
    return mean + amp * math.sin(2 * math.pi * (t / 3600.0 - 11.0) / 24.0)

# ── 제어기(1분 주기 호출, on/off 반환) ───────────────────
class Hysteresis:
    name = "A 히스테리시스 23/24(현행)"
    def __init__(self, on=23.0, off=24.0): self.on, self.off, self.state = on, off, False
    def __call__(self, t, y, t_out):
        if y < self.on: self.state = True
        elif y > self.off: self.state = False
        return self.state

class HystLead:
    """히스테리시스 + 선행 예측: y_pred = y + lag*기울기(최근 win분 선형회귀). 냉각 쪽 하강을 일찍 잡는다."""
    def __init__(self, lag_min, on=23.0, off=24.0, win=15, name=None):
        self.on, self.off, self.state = on, off, False
        self.lag = lag_min * 60.0; self.win = win * 60.0; self.h = collections.deque()
        self.name = name or "B 히스테리시스+선행예측"
    def slope(self):
        n = len(self.h)
        if n < 4: return 0.0
        mt = sum(x for x, _ in self.h) / n; my = sum(v for _, v in self.h) / n
        den = sum((x - mt) ** 2 for x, _ in self.h)
        return sum((x - mt) * (v - my) for x, v in self.h) / den if den > 0 else 0.0
    def __call__(self, t, y, t_out):
        self.h.append((t, y))
        while self.h and self.h[0][0] < t - self.win: self.h.popleft()
        yp = y + self.lag * self.slope()
        if yp < self.on: self.state = True
        elif yp > self.off: self.state = False
        return self.state

class PWMPI:
    """시간비례(PWM) + 피드포워드 + PI + Smith 예측기. 창(window)당 한 번 duty 결정, 창 안에서 ON을 앞쪽에 몰아 놓는다.
       모델(1노드, 제어기가 아는 UA·C — 실제와 다를 수 있음)로 '지연 없는 온도'를 예측해 PI에 먹인다."""
    def __init__(self, sp, lag_min, ua=47.0, c=1.55e6, p=1740.0, window_min=10, kp=0.5, ki=0.002, smith=True, name=None):
        self.sp, self.lag = sp, lag_min * 60.0
        self.ua, self.c, self.p = ua, c, p
        self.win = window_min * 60.0; self.kp, self.ki, self.smith = kp, ki, smith
        self.integ = 0.0; self.m_now = None; self.m_hist = collections.deque()
        self.u_prev = 0.0; self.win_start = None; self.duty = 0.0; self.t_last = None
        self.name = name or ("C PWM+PI+Smith" if smith else "C' PWM+PI")
    def __call__(self, t, y, t_out):
        dt = 60.0 if self.t_last is None else (t - self.t_last); self.t_last = t
        if self.m_now is None: self.m_now = y
        # 내부 모델을 직전 출력(u_prev, 0/1)으로 전진 (지연 없는 예측온도)
        self.m_now += (self.p * self.u_prev - self.ua * (self.m_now - t_out)) * dt / self.c
        self.m_hist.append((t, self.m_now))
        while len(self.m_hist) > 1 and self.m_hist[1][0] <= t - self.lag: self.m_hist.popleft()
        m_delayed = self.m_hist[0][1]
        fb = (y + (self.m_now - m_delayed)) if self.smith else y     # Smith: 실측 + (모델 무지연 − 모델 지연)
        if self.win_start is None or t - self.win_start >= self.win:
            e = self.sp - fb
            ff = max(0.0, min(1.0, self.ua * (self.sp - t_out) / self.p))
            self.integ = max(-0.3 / self.ki, min(0.3 / self.ki, self.integ + e * self.win))   # 안티와인드업
            self.duty = max(0.0, min(1.0, ff + self.kp * e + self.ki * self.integ))
            self.win_start = t
        on = (t - self.win_start) < self.duty * self.win
        self.u_prev = 1.0 if on else 0.0
        return on

# ── 시뮬레이션·지표 ─────────────────────────────────────
def simulate(ctrl, lag_min, hours=72, out_mean=-5.0, out_amp=5.0, dt=10.0, ua_true=47.0, seed=1, sp=23.5, warm_h=12, ca=2.0e5, ua_am=400.0):
    plant = Plant(ua=ua_true, ca=ca, cm=1.55e6 - ca, ua_am=ua_am, t0=sp); sensor = Sensor(lag_min, seed=seed)
    t = 0.0; on = False; next_ctl = 0.0
    n_sw = 0; wh = 0.0; tail = []
    for _ in range(int(hours * 3600 / dt)):
        t_out = outdoor(t, out_mean, out_amp)
        if t >= next_ctl:
            y = sensor.read(t, plant.ta)
            new = bool(ctrl(t, y, t_out))
            if new != on and t > warm_h * 3600: n_sw += 1
            on = new; next_ctl += 60.0
        else:
            sensor.read(t, plant.ta)
        plant.step(dt, on, t_out)
        if t > warm_h * 3600:
            wh += (plant.p if on else 0.0) * dt / 3600.0
            tail.append(plant.ta)
        t += dt
    days = (hours - warm_h) / 24.0
    n = len(tail); mean = sum(tail) / n
    def maxdrop(w):      # 최근 w분 내 최대 하강폭(°C) — 추운 쪽 불쾌 지표
        k = int(w * 60 / dt); best = 0.0; mx = collections.deque()
        for i, v in enumerate(tail):
            while mx and tail[mx[-1]] <= v: mx.pop()
            mx.append(i)
            while mx[0] <= i - k: mx.popleft()
            best = max(best, tail[mx[0]] - v)
        return best
    return {"mean": mean, "min": min(tail), "max": max(tail),
            "rms": math.sqrt(sum((v - sp) ** 2 for v in tail) / n),
            "in05": 100.0 * sum(1 for v in tail if abs(v - sp) <= 0.5) / n,
            "drop15": maxdrop(15), "drop60": maxdrop(60),
            "kwh_day": wh / 1000.0 / days, "sw_day": n_sw / days}

def make(lag, ua_err, window=10):
    ua_m = 47.0 * (1 + ua_err)
    return [
        Hysteresis(23.0, 24.0),
        HystLead(lag, 23.0, 24.0),
        HystLead(lag, 23.2, 23.8, name="B2 선행예측+좁은밴드 23.2/23.8"),
        PWMPI(23.5, lag, ua=ua_m, window_min=window, smith=False),
        PWMPI(23.5, lag, ua=ua_m, window_min=window, smith=True),
    ]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lag", type=float, default=5.0, help="센서 지연(분)")
    ap.add_argument("--ua-err", type=float, default=0.0, help="제어기 UA 모델 오차 비율(예 0.2=+20%%)")
    ap.add_argument("--window", type=float, default=10, help="PWM 창(분)")
    ap.add_argument("--ca", type=float, default=2.0e5, help="공기노드 열용량 J/K (작을수록 공기가 빨리 변함)")
    ap.add_argument("--ua-am", type=float, default=400.0, help="공기↔건물질량 결합 W/K (클수록 공기가 질량에 묶임)")
    ap.add_argument("--out", type=float, nargs="*", default=[-10.0], help="외기 평균들")
    ap.add_argument("--hours", type=float, default=72)
    a = ap.parse_args()
    for out_mean, out_amp in [(m, 5.0) for m in a.out]:
        print("\n■ 외기 %+.0f°C±%.0f 지연%.0f분 UA오차%+.0f%% PWM창%.0f분 Ca=%.0e 결합=%.0fW/K" % (out_mean, out_amp, a.lag, a.ua_err * 100, a.window, a.ca, a.ua_am))
        print("%-32s %5s %5s %5s %6s %7s %6s %6s %7s %6s" % ("제어기", "최저", "최고", "RMS", "±0.5내%", "15분하강", "1h하강", "kWh/일", "개폐/일", "평균"))
        for c in make(a.lag, a.ua_err, a.window):
            r = simulate(c, a.lag, a.hours, out_mean, out_amp, ca=a.ca, ua_am=a.ua_am)
            print("%-32s %5.2f %5.2f %5.2f %6.1f %7.2f %6.2f %6.1f %7.0f %6.2f" % (
                c.name, r["min"], r["max"], r["rms"], r["in05"], r["drop15"], r["drop60"], r["kwh_day"], r["sw_day"], r["mean"]))

if __name__ == "__main__":
    main()
