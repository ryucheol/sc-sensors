#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""다락 센서 플래시 전/후 검증. 사용: python3 verify_attic.py [분=60]
   - 저널 [수신] 줄에서 다락(인덱스2) 프레임 수·온도 값갱신 간격
   - latest.json 다락 신선도, batt_q 최근 배터리, heater_status delta, 1층 대비 오프셋
   - 이력 sync 로그에 다락이 없는지"""
import re, sys, json, os, time, sqlite3, subprocess, statistics, datetime
MIN = int(sys.argv[1]) if len(sys.argv) > 1 else 60
BASE = os.path.expanduser("~/smart-cabin")

out = subprocess.run(["journalctl", "-u", "farmhut-gateway.service", "--since", "%d min ago" % MIN,
                      "-o", "cat", "--no-pager"], capture_output=True, text=True).stdout.splitlines()
rx = [l for l in out if "[수신]" in l and "|" in l]
def ts(s):
    m = re.match(r"\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\]", s)
    return datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S") if m else None
frames = 0; tchg = []; hchg = []; last_vals = None
for l in rx:
    d = ts(l)
    if not d: continue
    tp, hp = l.split("[수신]", 1)[1].split("|", 1)
    tt = tp.split(); ht = hp.split()
    if len(tt) >= 3 and "(" in tt[2]:
        tchg.append((d, float(tt[2].split("(")[0])))
    if len(ht) >= 3 and "(" in ht[2]:
        hchg.append((d, float(ht[2].split("(")[0])))
    frames += "".join(ht[3:]).count("2")
    if len(tt) >= 3 and len(ht) >= 3:
        last_vals = (tt[2].split("(")[0], ht[2].split("(")[0])
# 동일값 중복 제거 후 간격
c = []
for d, v in sorted(tchg):
    if c and c[-1][1] == v and (d - c[-1][0]).total_seconds() < 120: continue
    c.append((d, v))
gaps = [(c[i+1][0] - c[i][0]).total_seconds() / 60 for i in range(len(c) - 1)]
print("=== 다락 수신 (최근 %d분) ===" % MIN)
print("프레임 %d개 (%.2f/분)  온도스텝 %d  습도변화 %d  마지막값 T/H = %s" % (frames, frames / MIN, len(c), len(hchg), last_vals))
if gaps:
    print("온도 값갱신 간격(분): 중앙값 %.1f  평균 %.1f  min %.1f  max %.1f" %
          (statistics.median(gaps), statistics.mean(gaps), min(gaps), max(gaps)))
else:
    print("온도 값갱신 간격: 스텝 부족(값 변화 없음 또는 미수신)")
if frames == 0:
    print("⚠ 다락 프레임 0 — 미수신 또는 복호화 실패(bindkey 불일치?) 의심")

# latest.json
try:
    lj = json.load(open(os.path.join(BASE, "latest.json")))
    a = lj.get("다락", {})
    age = time.time() - a["time_ms"] / 1000 if a.get("time_ms") else None
    print("latest.json 다락: temp=%s hum=%s age=%s" % (a.get("temp"), a.get("hum"), ("%.0fs" % age) if age else "?"))
    f = lj.get("내부1층", {})
    if a.get("temp") is not None and f.get("temp") is not None:
        print("다락−1층 현재 오프셋: %+.1f°C" % (a["temp"] - f["temp"]))
except Exception as e:
    print("latest.json 읽기 실패:", e)
# delta 학습값
try:
    hs = json.load(open(os.path.join(BASE, "heater_status.json")))
    print("heater_status delta_attic_floor(학습): %s" % hs.get("delta_attic_floor"))
except Exception as e:
    print("heater_status 읽기 실패:", e)
# 배터리
try:
    db = sqlite3.connect(os.path.join(BASE, "gateway_queue.db"))
    rows = db.execute("SELECT time_ms, volt, pct FROM batt_q WHERE sensor=? ORDER BY time_ms DESC LIMIT 3", ("다락",)).fetchall()
    for t, v, p in rows:
        print("batt_q 다락: %s  %.2fV %s%%" % (datetime.datetime.fromtimestamp(t / 1000).strftime("%m-%d %H:%M"), v, p))
except Exception as e:
    print("batt_q 읽기 실패:", e)
# 이력 sync에 다락이 끼어있는지
hist = [l for l in out if "이력" in l and "다락" in l]
print("이력 sync 로그 중 다락 언급: %d건 %s" % (len(hist), "(정상: 스킵됨)" if not hist else "⚠"))
