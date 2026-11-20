#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""thermal_fit.py — 2노드 열모델 피팅(docs/THERMAL_MODEL.ko.md §10).

  C1·dT1/dt = P − UA1·(T1−To) − (G12 + Gfan·fan)·(T1−T2) + a·I + Qocc·occ
  C2·dT2/dt = (G12 + Gfan·fan)·(T1−T2) − UA2·(T2−To) + a2·I

데이터: thermal_log.csv(Pi 1분), kma_obs.csv(시간, 대전 일사 si_133·부여 일조 ss). 10분 격자로 묶고,
30분마다 출발해 실측 입력(P·To·I·fan·occ)으로 H스텝 앞을 시뮬레이션한 오차를 최소자승(scipy).
검증: 홀드아웃 날짜의 1 h·6 h 선행 RMSE, 상수 모델(1노드 UA 47·τ 12 h·효율 0.62)과 비교, 23 °C 도달 시간.
Pi엔 scipy가 없으므로 CSV를 PC로 가져와 돌린다:
  scp pi:smart-cabin/{thermal_log.csv,kma_obs.csv} .
  python docs/thermal_fit.py [데이터폴더] [--holdout 2026-11-18] [--h 6] [--exclude 시작,끝 ...]
"""
import argparse, os
import numpy as np, pandas as pd
from scipy.optimize import least_squares

DT = 600.0
NAMES = ["C1(MJ/K)", "C2(MJ/K)", "UA1(W/K)", "UA2(W/K)", "G12(W/K)", "a(m2)", "Qocc(W)", "Gfan(W/K)", "a2(m2)"]
X0 = [1.2, 0.8, 30, 17, 60, 2.0, 100, 60, 0.5]
LO = [0.1, 0.05, 1, 1, 1, 0, 0, 0, 0]
HI = [10, 10, 200, 200, 1000, 20, 1000, 1000, 20]
# 측정 안 된 열원이 있던 구간(보조 히터를 Pi가 모르는 콘센트에서 켬 등) — 끝 뒤 1시간까지 뺀다
EXCLUDE = [("2026-11-15 20:20", "2026-11-15 22:40")]


def load(folder, exclude):
    d = pd.read_csv(os.path.join(folder, "thermal_log.csv"), parse_dates=["ts"]).set_index("ts")
    d["occ"] = d["mode"].isin(["comfort", "sleep"]).astype(float)   # 난방·취침 = 재실(원격 예열도 포함되는 근사)
    g = d[["watt", "t1", "t2", "to", "fan", "occ"]].apply(pd.to_numeric, errors="coerce").resample("10min").mean()
    g["to"] = g["to"].interpolate(limit=3)
    bad = np.zeros(len(g), bool)
    for a, b in exclude:
        bad |= (g.index >= a) & (g.index <= b)

    # 일사 W/m²: 대전 si_133(MJ/m²/h). 결측이면 부여 일조(ss)로 회귀 추정(맑은 하늘 곡선 × (b0 + b1·ss))
    k = pd.read_csv(os.path.join(folder, "kma_obs.csv"), parse_dates=["tm"]).drop_duplicates("tm").set_index("tm").sort_index()
    si = pd.to_numeric(k["si_133"], errors="coerce") * 1e6 / 3600
    ss = pd.to_numeric(k["ss"], errors="coerce").fillna(0)
    hr = k.index.hour
    clear = pd.Series(np.clip(np.sin(np.pi * (hr - 7.3) / 9.9), 0, None) * 560, index=k.index)   # 11월 부여 대략
    both = si.notna() & (clear > 50)
    b = np.linalg.lstsq(np.vstack([np.ones(both.sum()), ss[both]]).T, (si[both] / clear[both]).clip(0, 1.2), rcond=None)[0]
    est = clear * np.clip(b[0] + b[1] * ss, 0, 1.2)
    I = si.copy(); I[si.isna()] = est[si.isna()]; I = I.fillna(0)
    I.index = I.index - pd.Timedelta(minutes=30)          # 시각 라벨 = 직전 1시간 → 가운데로
    g["I"] = I.reindex(g.index.union(I.index)).interpolate(limit_area="inside").reindex(g.index).fillna(0)
    print("일사: si/clear = %.2f + %.2f·ss 로 낮 %d시간 추정" % (b[0], b[1], int((si.isna() & (clear > 50)).sum())))
    ok = ~(g["t1"].isna() | g["t2"].isna() | g["to"].isna()).values & ~bad
    return g, ok


class Model:
    def __init__(self, g, ok):
        self.idx = g.index; self.ok = ok
        self.P = g["watt"].fillna(0).values; self.T1 = g["t1"].values; self.T2 = g["t2"].values
        self.To = g["to"].values; self.I = g["I"].values
        self.occ = g["occ"].fillna(0).values; self.fan = g["fan"].fillna(0).values

    def step(self, x, t1, t2, i, sub=10):
        C1, C2, UA1, UA2, G12, a, Q, Gf, a2 = x
        G = G12 + Gf * self.fan[i]; h = DT / sub
        for _ in range(sub):
            q = G * (t1 - t2)
            d1 = (self.P[i] - UA1 * (t1 - self.To[i]) - q + a * self.I[i] + Q * self.occ[i]) / (C1 * 1e6)
            d2 = (q - UA2 * (t2 - self.To[i]) + a2 * self.I[i]) / (C2 * 1e6)
            t1 += d1 * h; t2 += d2 * h
        return t1, t2

    def starts(self, mask, H):
        return [j for j in range(0, len(self.idx) - H, 3) if mask[j] and self.ok[j:j + H + 1].all()]

    def resid(self, x, S, H):
        r = []
        for j in S:
            t1, t2 = self.T1[j], self.T2[j]
            for k in range(H):
                t1, t2 = self.step(x, t1, t2, j + k)
                r += [t1 - self.T1[j + k + 1], 0.7 * (t2 - self.T2[j + k + 1])]
        return np.array(r)

    def err(self, x, S, H):
        e1, e2 = [], []
        for j in S:
            t1, t2 = self.T1[j], self.T2[j]
            for k in range(H): t1, t2 = self.step(x, t1, t2, j + k)
            e1.append(t1 - self.T1[j + H]); e2.append(t2 - self.T2[j + H])
        e1, e2 = np.array(e1), np.array(e2)
        return np.sqrt(np.mean(e1 ** 2)), np.mean(e1), np.sqrt(np.mean(e2 ** 2))

    def base_err(self, S, H, UA=47.0, tau_h=12.0, eff=0.62):
        C = UA * tau_h * 3600; e = []
        for j in S:
            t = self.T1[j]
            for k in range(H):
                i = j + k; t += (eff * self.P[i] - UA * (t - self.To[i])) / C * DT
            e.append(t - self.T1[j + H])
        e = np.array(e); return np.sqrt(np.mean(e ** 2)), np.mean(e)

    def warmups(self, x, target=23.0, hold=None):
        on = self.P > 500
        for j in range(6, len(self.P)):
            if not (on[j] and not on[j - 6:j].any() and self.ok[j]) or self.T1[j] >= target - 1: continue
            end = min(j + 72, len(self.T1) - 1)
            act = next((k for k in range(j, end) if self.T1[k] >= target), None)
            s1, s2, pred = self.T1[j], self.T2[j], None
            for k in range(j, end):
                s1, s2 = self.step(x, s1, s2, k)
                if s1 >= target: pred = k + 1; break
            f = lambda k: "%d분" % ((k - j) * 10) if k else "12h 안에 도달 안 함"
            print("  %s 1층 %.1f·외기 %.1f → %.0f°C: 실제 %s, 예측 %s%s" % (
                self.idx[j].strftime("%m-%d %H:%M"), self.T1[j], self.To[j], target, f(act), f(pred),
                " (홀드아웃)" if hold is not None and self.idx[j] >= hold else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", nargs="?", default=".")
    ap.add_argument("--holdout", default="2026-11-18", help="이 날짜부터 검증용(학습에서 뺌)")
    ap.add_argument("--h", type=int, default=6, help="학습 선행 스텝(10분 단위, 6=1시간)")
    ap.add_argument("--exclude", nargs="*", default=None, help="'시작,끝' 형식 추가 제외 구간")
    a = ap.parse_args()
    exc = EXCLUDE + [tuple(s.split(",")) for s in (a.exclude or [])]
    g, ok = load(a.folder, exc)
    m = Model(g, ok)
    hold = pd.Timestamp(a.holdout); test = m.idx >= hold; train = ~test
    S = m.starts(train, a.h)
    res = least_squares(m.resid, X0, bounds=(LO, HI), args=(S, a.h), x_scale="jac", max_nfev=200)
    x = res.x
    try:
        se = np.sqrt(np.diag(np.linalg.inv(res.jac.T @ res.jac) * (res.fun ** 2).sum() / max(1, len(res.fun) - len(x))))
    except np.linalg.LinAlgError:
        se = np.full(len(x), np.nan)
    print("\n학습 %s ~ %s, 출발점 %d개, 선행 %d분" % (m.idx[0].date(), (hold - pd.Timedelta(days=1)).date(), len(S), a.h * 10))
    for n, v, e in zip(NAMES, x, se): print("  %-10s %8.3f ± %.3f" % (n, v, e))
    C1, C2, UA1, UA2, G12 = x[:5]
    print("  → 1층 기준 유효 UA ≈ %.0f W/K, 전체 C %.2f MJ/K, τ1 = C1/(UA1+G12) %.1f h" % (
        UA1 + 1 / (1 / max(UA2, 1e-9) + 1 / G12), C1 + C2, C1 * 1e6 / (UA1 + G12) / 3600))
    print("\n1층 RMSE(편향) · 다락 RMSE — 상수 모델(1층) 비교")
    for nm, msk in (("학습", train), ("홀드아웃", test)):
        for H in (6, 36):
            St = m.starts(msk, H)
            if not St: continue
            r1, b1, r2 = m.err(x, St, H); rb, bb = m.base_err(St, H)
            print("  %-6s %dh: 2노드 %.2f(%+.2f) 다락 %.2f | 상수 %.2f(%+.2f)  n=%d" % (nm, H // 6, r1, b1, r2, rb, bb, len(St)))
        Sh = [j for j in m.starts(msk, 6) if m.P[j:j + 6].mean() > 500]
        if Sh:
            r1, b1, _ = m.err(x, Sh, 6); rb, bb = m.base_err(Sh, 6)
            print("  %-6s 가열 중 1h: 2노드 %.2f(%+.2f) | 상수 %.2f(%+.2f)  n=%d" % (nm, r1, b1, rb, bb, len(Sh)))
    print("\n23°C 도달 시간(꺼져 있다 켠 때)")
    m.warmups(x, hold=hold)


if __name__ == "__main__":
    main()
