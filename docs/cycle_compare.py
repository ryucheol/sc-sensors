#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""히터 사이클 품질 비교(순정 12분 센서 vs pvvx 1분 센서).
   입력: thermal_backfill.csv + thermal_log.csv(1분 T1·relay), heater 저널(모드 전환·설정값).
   사이클 = ON 시작 ~ 다음 ON 시작. 지표: 길이, ON분, OFF 시점 T1(오버슈트 = 이후 최고−off_th), ON 시점 T1(언더슈트 = on_th−이후 최저), 스윙."""
import csv, subprocess, re, datetime, statistics, sys, os
os.chdir(os.path.expanduser("~/smart-cabin"))

rows = {}
for fn in ("thermal_backfill.csv", "thermal_log.csv"):
    if not os.path.exists(fn): continue
    for r in csv.DictReader(open(fn)):
        if r["t1"]: rows[r["ts"][:16]] = r
ts_sorted = sorted(rows)
def t1(ts):
    try: return float(rows[ts]["t1"])
    except: return None

# 히터 전환 이벤트 + 당시 밴드(comfort 1층 기준만)
ev = []
rx = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d):\d\d\] (내부1층) [\d.]+°C(?:\(\w+\))? \[comfort ([\d.]+)/([\d.]+)\] → 플러그 (ON|OFF)")
out = subprocess.run(["journalctl", "-u", "heater-control", "--no-pager", "-o", "cat", "--since", "-10d"], capture_output=True, text=True).stdout
for line in out.splitlines():
    m = rx.match(line)
    if m: ev.append((m.group(1), float(m.group(3)), float(m.group(4)), m.group(5) == "ON"))

def series(a, b):
    return [(ts, t1(ts)) for ts in ts_sorted if a <= ts < b and t1(ts) is not None]

cycles = []
for i, (ts, on_th, off_th, is_on) in enumerate(ev):
    if not is_on or on_th != 23.0 or off_th != 23.5: continue      # 23/23.5 컴포트 사이클만 비교
    # 다음 OFF, 그 다음 ON
    j = next((k for k in range(i + 1, len(ev)) if not ev[k][3]), None)
    n = next((k for k in range(i + 1, len(ev)) if ev[k][3]), None)
    if j is None or n is None or n < j: continue
    t_on, t_off, t_next = ts, ev[j][0], ev[n][0]
    f = lambda s: datetime.datetime.strptime(s, "%Y-%m-%d %H:%M")
    on_min = (f(t_off) - f(t_on)).total_seconds() / 60; cyc_min = (f(t_next) - f(t_on)).total_seconds() / 60
    if cyc_min > 240: continue                                         # 모드 전환 등으로 끊긴 사이클 제외
    s_off = series(t_off, t_next); s_on = series(t_on, t_off)
    if len(s_off) < 3 or len(s_on) < 2: continue
    peak = max(v for _, v in s_off); trough = min(v for _, v in s_off + s_on[:1])
    cycles.append(dict(start=t_on, on=on_min, cyc=cyc_min, over=peak - off_th, under=on_th - trough, swing=peak - trough,
                       t_at_off=t1(t_off), t_at_on=t1(t_on)))

def summ(cs, label):
    if not cs: print(label, "사이클 없음"); return
    med = lambda k: statistics.median(c[k] for c in cs)
    print("%-26s n=%d  사이클 %4.0f분  ON %3.0f분(%2.0f%%)  오버슈트 %+.2f  언더슈트 %+.2f  스윙 %.2f" %
          (label, len(cs), med("cyc"), med("on"), 100 * med("on") / med("cyc"), med("over"), med("under"), med("swing")))

cut = "2026-11-08 07:24"   # 1층 pvvx 전환
before = [c for c in cycles if c["start"] < cut]; after = [c for c in cycles if c["start"] >= cut]
print("== 컴포트 23/23.5, 1층 제어 사이클 ==")
summ(before, "순정 (12분 갱신)"); summ(after, "pvvx (1분 갱신)")
print("\n-- 개별 사이클 --")
for c in cycles:
    print("%s  %s  사이클 %3.0f분 ON %3.0f분  OFF시점 %.1f→최고 +%.2f  ON시점 %.1f→최저 −%.2f  스윙 %.2f" %
          (c["start"], "pvvx" if c["start"] >= cut else "순정", c["cyc"], c["on"], c["t_at_off"], c["over"], c["t_at_on"], c["under"], c["swing"]))
