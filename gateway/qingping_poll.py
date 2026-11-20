#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Qingping CO2 모니터(CGP22C) — 클라우드 Open API에서 1분 기록을 10분마다 받아 저장. 표준 라이브러리만.

기기는 1분마다 재고 10분마다 Wi-Fi(Ryufarm)로 클라우드에 올린다(BLE 광고도 10분 간격이라 클라우드가 주 경로).
cron: 3-59/10 * * * *  cd ~/smart-cabin && python3 gateway/qingping_poll.py >> qingping.log 2>&1

파일(모두 gitignore):
  qingping_key.json     App Key/Secret (developer.qingping.co) — 커밋 금지
  qingping_token.json   접근 토큰 캐시(2시간)
  qingping_log.csv      ts,temp,hum,co2,battery (1분)
  qingping_latest.json  마지막 값 {ts, temp, hum, co2, battery, fetched} — 상태 답장·시트가 읽음
사용:
  python3 gateway/qingping_poll.py            # 새 기록 받기(cron)
  python3 gateway/qingping_poll.py devices    # 기기 목록·마지막 값
"""
import base64, csv, datetime, gzip, json, os, sys, time, urllib.error, urllib.parse, urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
KEYS = os.path.join(BASE, "qingping_key.json")
TOKEN = os.path.join(BASE, "qingping_token.json")
LOG = os.path.join(BASE, "qingping_log.csv")
LATEST = os.path.join(BASE, "qingping_latest.json")
API = "https://apis.cleargrass.com/v1/apis"
COLS = ["ts", "temp", "hum", "co2", "battery"]
FIRST_LOOKBACK_S = 3600      # 처음엔 1시간만(집에 있던 때 기록을 섞지 않게)
MAX_LOOKBACK_S = 2 * 86400   # 끊겼다 돌아오면 최대 2일 메움


def token(now=None):
    now = now or time.time()
    try:
        t = json.load(open(TOKEN))
        if t.get("exp", 0) > now + 120:
            return t["tok"]
    except Exception:
        pass
    k = json.load(open(KEYS, encoding="utf-8-sig"))
    basic = base64.b64encode(("%s:%s" % (k["app_key"], k["app_secret"])).encode()).decode()
    body = urllib.parse.urlencode({"grant_type": "client_credentials", "scope": "device_full_access"}).encode()
    req = urllib.request.Request("https://oauth.cleargrass.com/oauth2/token", data=body, method="POST",
                                 headers={"Authorization": "Basic " + basic,
                                          "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.loads(r.read().decode())
    with open(TOKEN, "w") as f:
        json.dump({"tok": d["access_token"], "exp": now + int(d.get("expires_in", 7200))}, f)
    os.chmod(TOKEN, 0o600)
    return d["access_token"]


def get(tok, path, **params):
    for attempt in range(4):   # 503/429(요청 과다)이면 쉬었다 재시도
        params["timestamp"] = int(time.time() * 1000)   # 요청마다 고유, 20초 유효
        req = urllib.request.Request("%s/%s?%s" % (API, path, urllib.parse.urlencode(params)),
                                     headers={"Authorization": "Bearer " + tok})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
            break
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or attempt == 3:
                raise
            time.sleep(3 * 2 ** attempt)
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8"))


def val(d, k):
    v = (d or {}).get(k)
    return v.get("value") if isinstance(v, dict) else v


def to_rows(data, after_ts):
    """API data 목록 → [(ts, temp, hum, co2, battery)] (순수). after_ts 이하·중복·시각 없음은 버림, 시각순."""
    seen, out = set(), []
    for r in data or []:
        ts = val(r, "timestamp")
        if ts is None or ts <= after_ts or ts in seen:
            continue
        seen.add(ts)
        out.append((int(ts), val(r, "temperature"), val(r, "humidity"), val(r, "co2"), val(r, "battery")))
    return sorted(out)


def window(last_ts, now):
    """받을 구간(start, end) (순수): 마지막 기록 다음부터, 처음이면 1시간, 너무 오래면 2일까지만."""
    if not last_ts:
        return now - FIRST_LOOKBACK_S, now
    return max(last_ts + 1, now - MAX_LOOKBACK_S), now


def last_logged():
    try:
        with open(LOG, encoding="utf-8") as f:
            last = f.read().strip().splitlines()[-1].split(",")[0]
        return datetime.datetime.strptime(last, "%Y-%m-%d %H:%M:%S").timestamp()
    except Exception:
        return 0


def fetch(tok, mac, t0, t1):
    rows, off = [], 0
    while True:
        r = get(tok, "devices/data", mac=mac, start_time=int(t0), end_time=int(t1), offset=off, limit=200)
        batch = r.get("data") or []
        rows += batch
        off += len(batch)
        if not batch or off >= r.get("total", 0):
            return rows
        time.sleep(1.0)


def device_mac(tok):
    k = json.load(open(KEYS, encoding="utf-8-sig"))
    if k.get("mac"):
        return k["mac"].replace(":", "").upper()
    devs = get(tok, "devices", offset=0, limit=50).get("devices", [])
    return devs[0]["info"]["mac"] if devs else None


def main(argv):
    tok = token()
    if len(argv) > 1 and argv[1] == "devices":
        for d in get(tok, "devices", offset=0, limit=50).get("devices", []):
            i, dd = d.get("info", {}), d.get("data", {})
            ts = val(dd, "timestamp")
            print(i.get("mac"), i.get("version"), datetime.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M") if ts else "-",
                  {k: val(dd, k) for k in ("temperature", "humidity", "co2", "battery")})
        return 0
    mac = device_mac(tok)
    if not mac:
        print("기기 없음"); return 1
    now = time.time(); last = last_logged()
    t0, t1 = window(last, now)
    rows = to_rows(fetch(tok, mac, t0, t1), last)
    if rows:
        new = not os.path.exists(LOG)
        with open(LOG, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(COLS)
            for ts, *v in rows:
                w.writerow([datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")] + v)
        ts, t, h, c, b = rows[-1]
        with open(LATEST + ".tmp", "w") as f:
            json.dump({"ts": ts, "temp": t, "hum": h, "co2": c, "battery": b, "fetched": int(now)}, f)
        os.replace(LATEST + ".tmp", LATEST)
    print("%s 새 기록 %d (구간 %s~)" % (time.strftime("%m-%d %H:%M"), len(rows),
                                     datetime.datetime.fromtimestamp(t0).strftime("%m-%d %H:%M")))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception as e:
        print("%s 오류: %s" % (time.strftime("%m-%d %H:%M"), e)); sys.exit(1)
