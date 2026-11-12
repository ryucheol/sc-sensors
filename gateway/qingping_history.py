#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Qingping 클라우드(Open API)에서 기기 이력을 받아 CSV로 저장. 표준 라이브러리만 사용.

키 파일(커밋 금지, gitignore): 저장소 루트 qingping_key.json 또는 ~/.qingping_keys.json
    {"app_key": "...", "app_secret": "..."}
  developer.qingping.co 로그인 → Developer Information Management → App Key / App Secret

사용:
  python qingping_history.py devices                  # 기기 목록(MAC·모델·마지막 값·보고 주기)
  python qingping_history.py history 7                # 모든 기기의 최근 7일 → qingping_<MAC>.csv
  python qingping_history.py history 2026-11-01 2026-11-09 [MAC]

API(공식 문서, 2026-11-09 확인):
  토큰  POST https://oauth.cleargrass.com/oauth2/token  Basic base64(key:secret), grant_type=client_credentials, scope=device_full_access, 2시간 유효
  목록  GET  https://apis.cleargrass.com/v1/apis/devices?timestamp=<ms>
  이력  GET  https://apis.cleargrass.com/v1/apis/devices/data?mac=&start_time=<s>&end_time=<s>&timestamp=<ms>&offset=&limit<=200
"""
import base64, csv, gzip, datetime, json, os, sys, time, urllib.error, urllib.parse, urllib.request

_CANDIDATES = [os.environ.get("QINGPING_KEYS", ""),
               os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "qingping_key.json"),   # 저장소 루트(gitignore)
               os.path.join(os.path.dirname(os.path.abspath(__file__)), "qingping_key.json"),
               os.path.expanduser("~/.qingping_keys.json")]
KEYS = next((p for p in _CANDIDATES if p and os.path.exists(p)), os.path.expanduser("~/.qingping_keys.json"))
API = "https://apis.cleargrass.com/v1/apis"
OUT = os.getcwd()   # CSV는 실행한 폴더에
FIELDS = ["temperature", "humidity", "co2", "battery", "pm25", "pm10", "tvoc", "pressure"]


def token():
    k = json.load(open(KEYS, encoding="utf-8-sig"))
    basic = base64.b64encode(("%s:%s" % (k["app_key"], k["app_secret"])).encode()).decode()
    body = urllib.parse.urlencode({"grant_type": "client_credentials", "scope": "device_full_access"}).encode()
    req = urllib.request.Request("https://oauth.cleargrass.com/oauth2/token", data=body, method="POST",
                                 headers={"Authorization": "Basic " + basic,
                                          "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())["access_token"]


def get(tok, path, **params):
    params["timestamp"] = int(time.time() * 1000)   # 요청마다 고유, 20초 유효
    url = "%s/%s?%s" % (API, path, urllib.parse.urlencode(params))
    for attempt in range(6):   # 503/429(요청 과다)이면 점점 길게 쉬었다 재시도
        req = urllib.request.Request(url.replace("timestamp=%d" % params["timestamp"], "timestamp=%d" % int(time.time() * 1000)),
                                     headers={"Authorization": "Bearer " + tok})
        try:
            r = urllib.request.urlopen(req, timeout=30); break
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or attempt == 5: raise
            time.sleep(2 * 2 ** attempt)
    with r:
        raw = r.read()
        if raw[:2] == bytes([0x1f, 0x8b]): raw = gzip.decompress(raw)   # 서버가 gzip으로 보냄
        return json.loads(raw.decode("utf-8"))


def devices(tok):
    out, off = [], 0
    while True:
        r = get(tok, "devices", offset=off, limit=50)
        out += r.get("devices", [])
        off += 50
        if off >= r.get("total", 0): return out


def history(tok, mac, t0, t1):
    rows, off = [], 0
    while True:
        r = get(tok, "devices/data", mac=mac, start_time=int(t0), end_time=int(t1), offset=off, limit=200)
        batch = r.get("data", [])
        rows += batch
        off += len(batch)
        if not batch or off >= r.get("total", 0): return rows
        time.sleep(1.0)


def val(d, k):
    v = d.get(k)
    return v.get("value") if isinstance(v, dict) else None


def parse_day(s, end=False):
    d = datetime.datetime.strptime(s, "%Y-%m-%d")
    return (d + datetime.timedelta(days=1)).timestamp() if end else d.timestamp()


def main():
    if not os.path.exists(KEYS):
        sys.exit("키 파일이 없습니다: %s  ({\"app_key\": \"...\", \"app_secret\": \"...\"})" % KEYS)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "devices"
    tok = token()
    devs = devices(tok)
    if cmd == "devices":
        for d in devs:
            i, s = d.get("info", {}), d.get("info", {}).get("setting", {})
            last = {k: val(d.get("data", {}), k) for k in FIELDS + ["timestamp"] if val(d.get("data", {}), k) is not None}
            ts = last.pop("timestamp", None)
            print("%s  %-24s fw %-8s 보고 %ss/수집 %ss  %s  %s" % (
                i.get("mac"), (i.get("product") or {}).get("en_name", ""), i.get("version"),
                s.get("report_interval"), s.get("collect_interval"),
                datetime.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M") if ts else "-", last))
        return
    if cmd != "history": sys.exit(__doc__)
    a = sys.argv[2:]
    if a and a[0].isdigit():
        t1 = time.time(); t0 = t1 - int(a[0]) * 86400; macs = a[1:]
    else:
        t0, t1 = parse_day(a[0]), parse_day(a[1], end=True); macs = a[2:]
    targets = [d["info"]["mac"] for d in devs] if not macs else [m.replace(":", "").upper() for m in macs]
    for mac in targets:
        # 한 번에 너무 긴 구간은 7일씩 나눠 받음(서버 부담·타임아웃 방지)
        rows, s = [], t0
        while s < t1:
            e = min(s + 7 * 86400, t1)
            rows += history(tok, mac, s, e)
            s = e
        seen, recs = set(), []
        for r in rows:
            ts = val(r, "timestamp")
            if ts is None or ts in seen: continue
            seen.add(ts)
            recs.append([datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")] + [val(r, k) for k in FIELDS])
        recs.sort()
        used = [i for i, k in enumerate(FIELDS) if any(x[i + 1] is not None for x in recs)]
        path = os.path.join(OUT, "qingping_%s_%s_%s.csv" % (mac, time.strftime("%Y%m%d", time.localtime(t0)), time.strftime("%Y%m%d", time.localtime(t1 - 1))))   # 기간별 파일(덮어쓰기 방지)
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f); w.writerow(["time"] + [FIELDS[i] for i in used])
            for x in recs: w.writerow([x[0]] + [x[i + 1] for i in used])
        span = "%s ~ %s" % (recs[0][0], recs[-1][0]) if recs else "없음"
        print("%s: %d행 (%s) → %s" % (mac, len(recs), span, path))


if __name__ == "__main__":
    main()
