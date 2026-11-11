#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""기상청 API허브 단기예보(getVilageFcst) → forecast.json 원자적 저장.

설계(TODO §1 원칙):
  - 제어기(heater_control.py)는 인터넷과 무관하게 forecast.json '만' 읽는다.
  - 이 수집기는 인터넷이 될 때(핫스팟 접속 중 등) 주기 실행(크론/타이머)한다.
  - 인증키는 저장소에 두지 않는다(순서대로 탐색):
      1) 환경변수  KMA_AUTH_KEY
      2) ~/smart-cabin/kma.env         (KMA_AUTH_KEY=....)
      3) ~/smart-cabin/kma_keys.json   {"authKey": "...."}
  - 실패/낡음 판정은 소비자(제어기) 쪽에서: forecast.json의 fetched_ms로 신선도 확인.

격자: 기본 nx=57, ny=99 (부여 내산면/동지집). GW_FC_NX/GW_FC_NY로 변경 가능.
사용: python3 forecast_fetch.py
"""
import os, sys, json, datetime, urllib.request, urllib.parse

BASE = os.path.expanduser("~/smart-cabin")
OUT  = os.environ.get("GW_FORECAST_FILE", os.path.join(BASE, "forecast.json"))
NX   = int(os.environ.get("GW_FC_NX", "57"))
NY   = int(os.environ.get("GW_FC_NY", "99"))
API  = "https://apihub.kma.go.kr/api/typ02/openApi/VilageFcstInfoService_2.0/getVilageFcst"
SCHED = [2, 5, 8, 11, 14, 17, 20, 23]   # 단기예보 발표시각(3시간)


def auth_key():
    k = os.environ.get("KMA_AUTH_KEY")
    if k:
        return k.strip()
    # kma.env: KMA_AUTH_KEY=.... (따옴표 허용)
    try:
        with open(os.path.join(BASE, "kma.env")) as f:
            for line in f:
                line = line.strip()
                if line.startswith("KMA_AUTH_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    try:
        with open(os.path.join(BASE, "kma_keys.json")) as f:
            return json.load(f)["authKey"].strip()
    except Exception:
        return None


def base_candidates(now):
    """최신 발표부터 과거로 최대 4개(발표 후 15분 지난 것만). API허브 지연 대비 역순 재시도."""
    out = []
    for h in sorted([h for h in SCHED
                     if (now - now.replace(hour=h, minute=0, second=0, microsecond=0)).total_seconds() >= 15 * 60],
                    reverse=True):
        out.append((now.strftime("%Y%m%d"), "%02d00" % h))
    y = now - datetime.timedelta(days=1)
    for h in sorted(SCHED, reverse=True):
        out.append((y.strftime("%Y%m%d"), "%02d00" % h))
    return out[:4]


def online():
    """인터넷 연결 확인(기상청 또는 1.1.1.1에 TCP 443). kma_sync.py도 이 함수를 쓴다."""
    import socket
    for h in ("apihub.kma.go.kr", "1.1.1.1"):
        try:
            c = socket.create_connection((h, 443), 2.5); c.close(); return True
        except Exception:
            pass
    return False


def fetch(key, bd, bt, rows="1000"):
    """한 발표분(bd YYYYMMDD, bt HHMM)의 item 목록. NODATA면 []. rows: numOfRows(5일치 전체는 2000)."""
    q = urllib.parse.urlencode({"authKey": key, "numOfRows": str(rows), "pageNo": "1",
                                "dataType": "JSON", "base_date": bd, "base_time": bt,
                                "nx": NX, "ny": NY})
    with urllib.request.urlopen(API + "?" + q, timeout=30) as r:
        raw = r.read().decode("utf-8", "replace")
    try:
        data = json.loads(raw)
    except Exception:
        raise RuntimeError("비JSON 응답(키 오류 가능): " + raw[:120])
    hdr = data.get("response", {}).get("header", {})
    code = hdr.get("resultCode")
    if code == "03":          # NODATA → 이전 발표로 재시도
        return []
    if code not in ("00", None):
        raise RuntimeError("API 오류 %s %s" % (code, hdr.get("resultMsg", "")))
    return data["response"]["body"]["items"]["item"]


def main():
    key = auth_key()
    if not key:
        print("인증키 없음: 환경변수 KMA_AUTH_KEY 또는 ~/smart-cabin/kma_keys.json 필요", file=sys.stderr)
        sys.exit(2)

    # 인터넷 미연결이면 가져오기 시도 자체를 안 함(즉시 종료, 대기·재시도 없음)
    if not online():
        print("오프라인 — 예보 갱신 건너뜀", file=sys.stderr); sys.exit(0)

    now = datetime.datetime.now()
    items, used, last = None, None, None
    for bd, bt in base_candidates(now):
        try:
            got = fetch(key, bd, bt)
            if got:
                items, used = got, (bd, bt); break
        except Exception as e:
            last = e
    if not items:
        print("예보 수신 실패: %s" % (last or "모든 발표시각 NODATA"), file=sys.stderr)
        sys.exit(1)

    hourly, daily = {}, {}
    for it in items:
        k = (it["fcstDate"], it["fcstTime"]); cat = it["category"]; v = it["fcstValue"]
        h = hourly.setdefault(k, {})
        if   cat == "TMP": h["tmp"] = float(v)
        elif cat == "SKY": h["sky"] = int(v)        # 1맑음 3구름많음 4흐림
        elif cat == "PTY": h["pty"] = int(v)        # 0없음 1비 2비/눈 3눈 4소나기
        elif cat == "POP": h["pop"] = int(v)        # 강수확률 %
        elif cat == "REH": h["reh"] = int(v)        # 습도 %
        elif cat == "WSD": h["wsd"] = float(v)      # 풍속 m/s
        elif cat == "TMN": daily.setdefault(it["fcstDate"], {})["tmn"] = float(v)
        elif cat == "TMX": daily.setdefault(it["fcstDate"], {})["tmx"] = float(v)

    rows = []
    for d, t in sorted(hourly):
        dt = datetime.datetime.strptime(d + t, "%Y%m%d%H%M")
        rec = dict(hourly[(d, t)], dt=dt.strftime("%Y-%m-%d %H:%M"), ms=int(dt.timestamp() * 1000))
        rows.append(rec)

    out = {"fetched_at": now.strftime("%Y-%m-%d %H:%M:%S"), "fetched_ms": int(now.timestamp() * 1000),
           "base": "%s %s" % used, "nx": NX, "ny": NY, "hourly": rows,
           "daily": {d: daily[d] for d in sorted(daily)}}
    tmp = OUT + ".tmp"
    with open(tmp, "w") as f:
        json.dump(out, f, ensure_ascii=False)
    os.replace(tmp, OUT)
    try:   # 예보 이력(forecast_hist.csv): 같은 발표분(base)은 한 번만. 열모델 학습·예보 채점용.
        hist = OUT.replace("forecast.json", "forecast_hist.csv")
        base = out["base"]; seen = False
        if os.path.exists(hist):
            with open(hist) as f:
                for line in f:
                    if line.startswith(base + ","): seen = True; break
        if not seen:
            newf = not os.path.exists(hist)
            with open(hist, "a") as f:
                if newf: f.write("base,dt,tmp,sky,pty,pop,reh,wsd\n")
                for r in rows:
                    f.write("%s,%s,%s,%s,%s,%s,%s,%s\n" % (base, r["dt"], r.get("tmp", ""), r.get("sky", ""),
                            r.get("pty", ""), r.get("pop", ""), r.get("reh", ""), r.get("wsd", "")))
    except Exception as e:
        print("forecast_hist 기록 실패: %s" % e, file=sys.stderr)

    tmn = min((v["tmn"] for v in daily.values() if "tmn" in v), default=None)
    print("저장 %s — 발표 %s, %d시간(%s~%s), 일최저 %s°C"
          % (os.path.basename(OUT), " ".join(used), len(rows),
             rows[0]["dt"] if rows else "-", rows[-1]["dt"] if rows else "-", tmn))


if __name__ == "__main__":
    main()
