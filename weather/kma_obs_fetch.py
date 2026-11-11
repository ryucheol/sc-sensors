#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""기상청 API허브 ASOS 시간자료(kma_sfctm3) → kma_obs.csv (열 모델 학습용 외부 변수).
   부여(236): 기온·습도·풍속·풍향·전운량·일조·지면온도. 부여엔 일사계가 없어(SI=-9) 대전(133) 일사를 si_133으로 함께 기록.
   cron 매시 15분. 최근 6시간을 받아 tm 기준 멱등 append. 인증키는 kma.env(KMA_AUTH_KEY) — 출력·로그에 찍지 않음."""
import os, sys, csv, datetime, urllib.request, urllib.parse

BASE = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
OUT = os.path.join(BASE, "kma_obs.csv")
URL = "https://apihub.kma.go.kr/api/typ01/url/kma_sfctm3.php"
STN_MAIN, STN_SI = "236", "133"
COLS = ["tm", "ta", "hm", "ws", "wd", "ca_tot", "ss", "si", "ts", "si_133"]
# kma_sfctm3 고정 열 순서(help=0): 0 TM 1 STN 2 WD 3 WS 4-6 GST 7 PA 8 PS 9 PT 10 PR 11 TA 12 TD 13 HM 14 PV
#   15-18 RN 19-21 SD 22 WC 23 WP 24 WW 25 CA_TOT 26 CA_MID 27 CH_MIN 28-31 CT 32 VS 33 SS 34 SI 35 ST_GD 36 TS ...
IDX = {"wd": 2, "ws": 3, "ta": 11, "hm": 13, "ca_tot": 25, "ss": 33, "si": 34, "ts": 36}
NTOK_MIN = 37


def auth_key():
    k = os.environ.get("KMA_AUTH_KEY")
    if k: return k.strip()
    try:
        with open(os.path.join(BASE, "kma.env")) as f:
            for line in f:
                line = line.strip()
                if line.startswith("KMA_AUTH_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return None


def fetch(key, stn, tm1, tm2):
    q = urllib.parse.urlencode({"tm1": tm1, "tm2": tm2, "stn": stn, "help": "0", "authKey": key})
    with urllib.request.urlopen(URL + "?" + q, timeout=30) as r:
        txt = r.read().decode("euc-kr", "replace")
    rows = {}
    for line in txt.splitlines():
        if not line or line.startswith("#"): continue
        t = line.split()
        if len(t) < NTOK_MIN or not t[0].isdigit(): continue
        rows[t[0]] = t
    return rows


def val(tok, i):
    try:
        v = float(tok[i])
    except Exception:
        return ""
    if v <= -9 or v <= -99: return ""          # 결측 -9 / -99
    return ("%g" % v)


def main():
    key = auth_key()
    if not key:
        print("KMA_AUTH_KEY 없음(kma.env)", file=sys.stderr); sys.exit(2)
    from forecast_fetch import online      # 같은 weather/ 폴더
    if not online():
        print("오프라인 — 관측 수집 건너뜀", file=sys.stderr); sys.exit(0)
    now = datetime.datetime.now()
    tm2 = now.strftime("%Y%m%d%H00"); tm1 = (now - datetime.timedelta(hours=6)).strftime("%Y%m%d%H00")
    if len(sys.argv) >= 2: tm1 = sys.argv[1]          # 백필: python3 kma_obs_fetch.py 202611010000 [202611080900]
    if len(sys.argv) >= 3: tm2 = sys.argv[2]
    main_rows = fetch(key, STN_MAIN, tm1, tm2)
    try:
        si_rows = fetch(key, STN_SI, tm1, tm2)
    except Exception as e:
        si_rows = {}; print("대전 일사 수신 실패: %s" % e, file=sys.stderr)
    have = set()
    if os.path.exists(OUT):
        with open(OUT, newline="") as f:
            for r in csv.DictReader(f): have.add(r["tm"])
    new = []
    for tm in sorted(main_rows):
        tm_s = datetime.datetime.strptime(tm, "%Y%m%d%H%M").strftime("%Y-%m-%d %H:%M")
        if tm_s in have: continue   # 멱등: CSV의 포맷된 tm과 비교(원시 tm과 비교하면 중복 기록됨)
        t = main_rows[tm]
        rec = {"tm": tm_s}
        for k, i in IDX.items(): rec[k] = val(t, i)
        rec["si_133"] = val(si_rows[tm], IDX["si"]) if tm in si_rows else ""
        new.append(rec)
    if new:
        newfile = not os.path.exists(OUT)
        with open(OUT, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLS)
            if newfile: w.writeheader()
            w.writerows(new)
    print("%s kma_obs: +%d행 (%s~%s)" % (now.strftime("%m-%d %H:%M"), len(new),
          new[0]["tm"] if new else "-", new[-1]["tm"] if new else "-"))


if __name__ == "__main__":
    main()
