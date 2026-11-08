#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""저널에 남아 있는 과거 [수신] 줄(1분 온·습도)과 heater-control 로그(ON/OFF·현황 W)로
   thermal_backfill.csv 를 만든다(thermal_log.csv 와 같은 열, 로거 투입 이전 구간용).
   relay/watt 는 히터 로그의 전환 이벤트로 복원(사이 구간은 마지막 상태 유지), 현황 줄의 W 로 보정."""
import subprocess, re, csv, datetime, os
BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "thermal_backfill.csv")

def journal(unit):
    return subprocess.run(["journalctl", "-u", unit, "--no-pager", "-o", "cat", "--since", "-30d"],
                          capture_output=True, text=True).stdout.splitlines()

# 센서 순서: 바닥외부, 내부1층, 다락  (게이트웨이 [수신] 줄 규약)
rx = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\] \[수신\] (\S+) (\S+) (\S+) \| (\S+) (\S+) (\S+)")
def num(s):
    s = re.sub(r"\(.*?\)", "", s)
    try: return float(s)
    except Exception: return ""

temps = {}   # minute -> (to,t1,t2,ho,h1,h2)
for line in journal("farmhut-gateway"):
    m = rx.match(line)
    if not m: continue
    ts = m.group(1)[:16]
    temps[ts] = tuple(num(m.group(i)) for i in (2, 3, 4, 5, 6, 7))

ev = []   # (ts, relay, watt, mode)
rx_sw = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\].*\[(\w+) [\d./]+\] → 플러그 (ON|OFF) \((\d+)W")
rx_st = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\] 현황: .*\[(\w+) [\d./]+\] 플러그 (ON|OFF) \((\d+)W")
for line in journal("heater-control"):
    m = rx_sw.match(line) or rx_st.match(line)
    if m: ev.append((m.group(1)[:16], m.group(3) == "ON", float(m.group(4)), m.group(2)))
ev.sort()

rows = []
j = 0; relay = 0; watt = 0.0; mode = ""
for ts in sorted(temps):
    while j < len(ev) and ev[j][0] <= ts:
        relay, watt, mode = (1 if ev[j][1] else 0), ev[j][2], ev[j][3]; j += 1
    to, t1, t2, ho, h1, h2 = temps[ts]
    rows.append([ts + ":00", mode, relay, "%.0f" % (watt if relay else 0), "", t1, h1, t2, h2, to, ho, "", "", ""])

with open(OUT, "w", newline="") as f:
    w = csv.writer(f); w.writerow("ts,mode,relay,watt,volt,t1,h1,t2,h2,to,ho,t_ctl,delta,fan".split(","))
    w.writerows(rows)
print("backfill %d행 %s ~ %s → %s" % (len(rows), rows[0][0] if rows else "-", rows[-1][0] if rows else "-", os.path.basename(OUT)))
