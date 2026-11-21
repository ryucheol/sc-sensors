#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""1층 샤오미(pvvx, 동창틀) ↔ Qingping(테이블) 온도 차이 분석 — 열모델용 센서 오차 보정.

배경(11-21): 샤오미는 동창틀에 붙어 맑은 아침 08~11시 햇빛에 +0.5~1.1°C, 밤엔 찬 유리 옆이라 −0.5°C.
안쪽 벽도 오차가 있어(사용자) 옮기지 않고, 1주일(11-21~28) 나란히 기록해 오차를 조건별로 재고 모델에 넣는다.
Qingping은 몸집이 커 빠른 변화엔 늦으므로(시정수 수십 분) '천천히 변할 때'만 기준으로 쓴다.

입력(Pi 로컬): thermal_log.csv(1분), qingping_log.csv(1분), kma_obs.csv(매시: ss 일조·si_133 대전 일사·ta)
출력: 표 + sensor_compare.csv(분별 짝: ts,t_x,t_q,d,dTq_10m,relay,fan,hour,ss,si,ta) → docs/thermal_fit.py가 읽어 보정
사용: python3 docs/sensor_compare.py [시작일 YYYY-MM-DD] [끝일]    (기본: 11-21부터 지금까지)
"""
import csv, datetime as D, os, statistics as st, sys

BASE = os.path.expanduser(os.environ.get("SMART_CABIN_DIR", "~/smart-cabin"))
SLOW = 0.15   # Qingping 10분 변화 |ΔT| ≤ 이 값(°C)일 때만 '정상 상태'로 보고 비교(빠른 변화 때 늦음 제거)


def load_q(path):
    out = {}
    for r in csv.DictReader(open(path, encoding="utf-8")):
        try:
            out[r["ts"][:16]] = float(r["temp"])
        except (ValueError, TypeError, KeyError):
            pass
    return out


def load_kma(path):
    out = {}
    try:
        for r in csv.DictReader(open(path, encoding="utf-8")):
            def f(k):
                try: return float(r.get(k) or "")
                except ValueError: return None
            out[r["tm"][:13]] = (f("ss"), f("si_133"), f("ta"))
    except OSError:
        pass
    return out


def pairs(thermal, q, kma, t0, t1):
    """분별 짝(순수에 가깝게: 입력 dict/rows) → list of dict."""
    out = []
    for r in thermal:
        k = r["ts"][:16]
        if not (t0 <= k[:10] <= t1) or k not in q or not r.get("t1"):
            continue
        prev = (D.datetime.strptime(k, "%Y-%m-%d %H:%M") - D.timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M")
        dq = (q[k] - q[prev]) if prev in q else None
        # 시간별 기상은 '그 시각까지의 1시간'이라 다음 정시 값을 붙임
        hk = (D.datetime.strptime(k[:13], "%Y-%m-%d %H") + D.timedelta(hours=1)).strftime("%Y-%m-%d %H")
        ss, si, ta = kma.get(hk, (None, None, None))
        tx = float(r["t1"])
        out.append({"ts": k, "t_x": tx, "t_q": q[k], "d": round(tx - q[k], 2), "dTq_10m": dq,
                    "relay": r.get("relay"), "fan": r.get("fan"), "hour": int(k[11:13]), "ss": ss, "si": si, "ta": ta})
    return out


def summarize(rows, label):
    v = [x["d"] for x in rows]
    if not v:
        return "%-28s  (자료 없음)" % label
    return "%-28s n=%5d  평균 %+.2f  중앙 %+.2f  표준편차 %.2f  범위 %+.1f~%+.1f" % (
        label, len(v), st.mean(v), st.median(v), st.pstdev(v), min(v), max(v))


def main(argv):
    t0 = argv[1] if len(argv) > 1 else "2026-11-21"
    t1 = argv[2] if len(argv) > 2 else "2099-12-31"
    thermal = list(csv.DictReader(open(os.path.join(BASE, "thermal_log.csv"), encoding="utf-8")))
    q = load_q(os.path.join(BASE, "qingping_log.csv"))
    kma = load_kma(os.path.join(BASE, "kma_obs.csv"))
    rows = pairs(thermal, q, kma, t0, t1)
    slow = [x for x in rows if x["dTq_10m"] is not None and abs(x["dTq_10m"]) <= SLOW]
    print("샤오미(1층 동창틀) − Qingping(테이블), %s ~ %s" % (t0, t1 if t1 < "2099" else "지금"))
    print(summarize(rows, "전체"))
    print(summarize(slow, "천천히 변할 때(기준)"))
    print(summarize([x for x in slow if x["ss"] is not None and x["ss"] >= 0.5 and 7 <= x["hour"] < 11], "맑은 아침 07~11시"))
    print(summarize([x for x in slow if x["ss"] is not None and x["ss"] >= 0.5 and 11 <= x["hour"] < 17], "맑은 낮 11~17시"))
    print(summarize([x for x in slow if (x["hour"] >= 22 or x["hour"] < 6)], "밤 22~06시"))
    print(summarize([x for x in slow if x["relay"] == "1"], "히터 켜짐"))
    print(summarize([x for x in slow if x["fan"] == "1"], "선풍기 켜짐"))
    print(summarize([x for x in slow if x["fan"] == "0"], "선풍기 꺼짐"))
    print("\n시각별(천천히 변할 때) 평균 차이")
    for h in range(24):
        v = [x["d"] for x in slow if x["hour"] == h]
        if v:
            print("  %02d시  %+.2f  (n=%d)%s" % (h, st.mean(v), len(v), "  " + "#" * min(40, int(abs(st.mean(v)) * 10))))
    out = os.path.join(BASE, "sensor_compare.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["ts"])
        w.writeheader(); w.writerows(rows)
    print("\n분별 짝 %d개 → %s" % (len(rows), out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
