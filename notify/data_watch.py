#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""농막 LTE 데이터 사용량 감시 → 텔레그램 (tplus 6GB 요금제 보호).

읽는 곳(우선순위):
  1) E3372 HiLink 관리 API(http://192.168.8.1): /api/monitoring/month_statistics 의 이번 달 다운+업로드(동글이 직접 셈)
  2) 동글을 못 읽으면 Pi의 인터넷 인터페이스(기본 경로 장치) rx+tx 바이트 증가분을 직접 누적(재부팅·카운터 리셋 대응)
경보(한 주기에 단계마다 한 번): 한도의 50 / 70 / 85 / 95 % + 폭주(최근 1시간 200MB 이상 = 동영상 의심).
상태 파일 data_usage.json 에 남겨 다른 도구(상태 명령 등)가 읽게 한다.
설정: heater_config.json 의 "data_watch" 로 덮어쓰기
  {"limit_gb": 6.0, "cycle_day": 1, "levels": [0.5,0.7,0.85,0.95], "burst_mb_per_h": 200, "hilink": "192.168.8.1"}
  ⚠ 개통 첫 달은 데이터가 일할 지급일 수 있다(11-11 개통) → 그달만 limit_gb 를 낮춰 둘 것.
실행: python3 notify/data_watch.py            # 1회 측정·판단·전송(systemd timer 10분)
      python3 notify/data_watch.py --dry-run  # 측정·판단만 출력(전송·상태 저장 안 함)
"""
import os, sys, json, time, datetime, re, subprocess, urllib.request

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import telegram_notify as tg   # noqa: E402

BASE = tg.BASE
CFG = os.path.join(BASE, "heater_config.json")
STATE = os.path.join(BASE, "data_watch_state.json")
OUT = os.path.join(BASE, "data_usage.json")
LOG = os.path.join(BASE, "data_watch.log")
DEFAULTS = {"enabled": True, "limit_gb": 6.0, "cycle_day": 1, "levels": [0.5, 0.7, 0.85, 0.95],
            "burst_mb_per_h": 200, "hilink": "192.168.8.1"}
GB = 1024 ** 3
MB = 1024 ** 2


def log(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def cfg():
    c = dict(DEFAULTS)
    try:
        c.update(json.load(open(CFG, encoding="utf-8")).get("data_watch", {}))
    except Exception:
        pass
    return c


# ── 순수 함수 ─────────────────────────────────────────
def cycle_start(today, cycle_day):
    """과금 주기 시작일(이번 주기). cycle_day=1 이면 매월 1일."""
    d = min(cycle_day, 28)
    if today.day >= d:
        return today.replace(day=d)
    first = today.replace(day=1) - datetime.timedelta(days=1)
    return first.replace(day=min(d, 28))


def xml_val(xml, tag):
    m = re.search(r"<%s>([^<]*)</%s>" % (tag, tag), xml)
    return m.group(1) if m else None


def tally(st, counter, cyc):
    """인터페이스 카운터(누적 바이트) → 이번 주기 사용량. 카운터가 줄면(재부팅·재연결) 새 기준으로 이어 붙인다."""
    if st.get("cycle") != cyc:
        st.update(cycle=cyc, used=0, last=counter)
        return 0
    last = st.get("last")
    if last is not None and counter >= last:
        st["used"] = st.get("used", 0) + (counter - last)
    st["last"] = counter
    return st["used"]


def levels_to_alert(used, limit, levels, sent):
    """새로 넘은 단계(한 주기에 한 번씩)."""
    return [lv for lv in levels if used >= lv * limit and str(lv) not in sent]


def burst(hist, now, used, window=3600):
    """최근 window초 동안 늘어난 바이트. hist=[(ts, used)], 오래된 것은 잘라 낸다."""
    hist.append((now, used))
    hist[:] = [h for h in hist if now - h[0] <= window + 900]
    old = [h for h in hist if now - h[0] >= window]
    base = old[-1][1] if old else hist[0][1]
    return max(0, used - base)


def fmt(b):
    return "%.2fGB" % (b / GB) if b >= GB else "%.0fMB" % (b / MB)


# ── 측정 ──────────────────────────────────────────────
def hilink_month(host, timeout=4):
    """E3372 HiLink 이번 달 사용량(바이트) 또는 None."""
    def get(path, hdr=None):
        req = urllib.request.Request("http://%s%s" % (host, path), headers=hdr or {})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    try:
        tok = get("/api/webserver/SesTokInfo")
        hdr = {"Cookie": xml_val(tok, "SesInfo") or "", "__RequestVerificationToken": xml_val(tok, "TokInfo") or ""}
        x = get("/api/monitoring/month_statistics", hdr)
        dn, up = xml_val(x, "CurrentMonthDownload"), xml_val(x, "CurrentMonthUpload")
        if dn is None or up is None:
            return None
        return int(dn) + int(up)
    except Exception:
        return None


def wan_counter():
    """기본 경로 인터페이스의 rx+tx 누적 바이트, (장치, 바이트) 또는 (None, None)."""
    try:
        out = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True, timeout=5).stdout
        dev = re.search(r"\bdev (\S+)", out).group(1)
        rd = lambda n: int(open("/sys/class/net/%s/statistics/%s" % (dev, n)).read())
        return dev, rd("rx_bytes") + rd("tx_bytes")
    except Exception:
        return None, None


def run(dry=False, now=None):
    c = cfg()
    if not c.get("enabled", True):
        return 0
    now = now or time.time()
    today = datetime.date.fromtimestamp(now)
    cyc = cycle_start(today, c["cycle_day"]).isoformat()
    try:
        st = json.load(open(STATE))
    except Exception:
        st = {}
    limit = c["limit_gb"] * GB

    src, used = "hilink", hilink_month(c["hilink"])
    dev, ctr = wan_counter()
    if ctr is not None:
        own = tally(st.setdefault("iface", {}), ctr, cyc)      # 항상 누적해 둔다(동글을 못 읽는 날 대비)
    if used is None:
        if ctr is None:
            log("측정 불가(동글·인터페이스 모두 실패)"); return 1
        src, used = "iface:%s" % dev, own

    if st.get("cycle") != cyc:
        st.update(cycle=cyc, sent=[], hist=[])
    new = levels_to_alert(used, limit, c["levels"], st.get("sent", []))
    b = burst(st.setdefault("hist", []), now, used)
    msgs = []
    for lv in new:
        msgs.append(("data_lv_%s" % lv, "📶 농막 데이터 %d%% 사용: %s / %.0fGB (%s 시작 주기). 동영상·업데이트 주의"
                     % (round(lv * 100), fmt(used), c["limit_gb"], cyc)))
    if b >= c["burst_mb_per_h"] * MB:
        msgs.append(("data_burst", "⚠ 농막 데이터 급증: 최근 1시간 %s (동영상·업데이트 의심). 이번 주기 누적 %s"
                     % (fmt(b), fmt(used))))
    out = {"updated": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)), "source": src, "cycle_start": cyc,
           "used_bytes": used, "used": fmt(used), "limit_gb": c["limit_gb"], "pct": round(100.0 * used / limit, 1),
           "last_hour": fmt(b)}
    if dry:
        print(json.dumps(out, ensure_ascii=False)); [print("보낼 메시지:", m) for _, m in msgs]
        return 0
    for key, m in msgs:
        tg.send(m, key=key, min_interval=3600 if key == "data_burst" else 0)
    st["sent"] = st.get("sent", []) + [str(lv) for lv in new]
    with open(STATE, "w") as f:
        json.dump(st, f)
    with open(OUT, "w") as f:
        json.dump(out, f, ensure_ascii=False)
    log("%s %s (%.1f%%) 최근1h %s%s" % (src, out["used"], out["pct"], out["last_hour"], " 경보 %d" % len(msgs) if msgs else ""))
    return 0


if __name__ == "__main__":
    sys.exit(run(dry="--dry-run" in sys.argv))
