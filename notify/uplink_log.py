#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wi-Fi 동글(ryuspot_d, ZTE MF79U) 상태 기록 — cron 매분. 11-20 오후 동글 LTE 먹통의 원인을 다음번에 잡기 위한 기록.

Pi가 Wi-Fi(wlan0)로 붙어 있을 때마다 한 줄: Wi-Fi 신호, 동글 LTE 상태(로그인 없이 읽히는 값), 동글까지·인터넷까지 응답.
LTE 6GB 동글(eth1)은 쓰지 않는다(모든 확인을 wlan0로만).
기록: uplink_log.csv (40일 넘은 줄은 하루 한 번 정리)
열: ts, ssid, wifi_sig, ppp, net, bars, rsrp, rsrq, snr, cell, gw_ms, inet_code, inet_ms
  ppp=동글 데이터 연결 상태(ppp_connected 등), gw_ms=Pi→동글 핑(ms, 실패 -1), inet_code=generate_204 응답(실패 000)
  rx_mb·tx_mb = 직전 기록 이후 wlan0로 받은·보낸 MB(Pi를 거친 농막 기기 전부 포함, 동글에 직접 붙은 폰 등은 빠짐),
  kbps = 그 사이 평균 속도(받음+보냄). 재부팅으로 카운터가 0이 되면 그 줄은 비움.
사용량 요약: python3 notify/uplink_log.py usage [YYYY-MM-DD]  → 시간별 MB·평균/최대 속도
"""
import csv, datetime as dt, json, os, re, subprocess, sys, time, urllib.request

HERE = os.path.dirname(os.path.realpath(__file__))
BASE = os.path.dirname(HERE)
OUT = os.path.join(BASE, "uplink_log.csv")
COLS = ["ts", "ssid", "wifi_sig", "ppp", "net", "bars", "rsrp", "rsrq", "snr", "cell", "gw_ms", "inet_code", "inet_ms",
        "rx_mb", "tx_mb", "kbps"]
BYTES_STATE = os.path.join(BASE, "uplink_bytes.json")   # 직전 wlan0 카운터(재부팅 감지용)
ZTE_GW = "192.168.0.1"


def sh(args, timeout=10):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


def wifi():
    """(ssid, 신호%, 게이트웨이) 또는 None."""
    out = sh(["nmcli", "-t", "-f", "GENERAL.STATE,GENERAL.CONNECTION,IP4.GATEWAY", "device", "show", "wlan0"]).splitlines()
    kv = dict(l.split(":", 1) for l in out if ":" in l)
    if not kv.get("GENERAL.STATE", "").startswith("100"):
        return None
    sig = ""
    for l in sh(["nmcli", "-t", "-f", "IN-USE,SIGNAL", "dev", "wifi", "list", "--rescan", "no"]).splitlines():
        if l.startswith("*:"):
            sig = l.split(":")[1]
    return kv.get("GENERAL.CONNECTION", ""), sig, kv.get("IP4.GATEWAY", "")


def zte():
    cmd = "ppp_status,network_type,signalbar,lte_rsrp,lte_rsrq,lte_snr,cell_id"
    try:
        req = urllib.request.Request("http://%s/goform/goform_get_cmd_process?multi_data=1&cmd=%s" % (ZTE_GW, cmd),
                                     headers={"Referer": "http://%s/index.html" % ZTE_GW})
        d = json.loads(urllib.request.urlopen(req, timeout=5).read().decode())
    except Exception as e:
        return {"ppp": "api_err:%s" % type(e).__name__}
    return {"ppp": d.get("ppp_status", ""), "net": d.get("network_type", ""), "bars": d.get("signalbar", ""),
            "rsrp": d.get("lte_rsrp", ""), "rsrq": d.get("lte_rsrq", ""), "snr": d.get("lte_snr", ""), "cell": d.get("cell_id", "")}


def ping_ms(host):
    m = re.search(r"time=([\d.]+)", sh(["ping", "-c", "1", "-W", "2", "-I", "wlan0", host], timeout=5))
    return m.group(1) if m else "-1"


def inet():
    out = sh(["curl", "-s", "-4", "-m", "8", "--interface", "wlan0", "-o", "/dev/null", "-w", "%{http_code} %{time_total}",
              "http://connectivitycheck.gstatic.com/generate_204"], timeout=12).split()
    return (out[0], "%.0f" % (float(out[1]) * 1000)) if len(out) == 2 else ("000", "")


def counters(iface="wlan0"):
    try:
        r = int(open("/sys/class/net/%s/statistics/rx_bytes" % iface).read())
        t = int(open("/sys/class/net/%s/statistics/tx_bytes" % iface).read())
        return r, t
    except (OSError, ValueError):
        return None


def traffic(prev, cur, now):
    """(rx_mb, tx_mb, kbps) 문자열 — 순수. prev/cur = (ts, rx, tx). 카운터가 줄었거나(재부팅) 간격이 이상하면 빈칸."""
    if not prev or not cur:
        return "", "", ""
    dt_s = cur[0] - prev[0]; dr = cur[1] - prev[1]; dtx = cur[2] - prev[2]
    if dt_s <= 0 or dt_s > 600 or dr < 0 or dtx < 0:
        return "", "", ""
    return "%.2f" % (dr / 1e6), "%.2f" % (dtx / 1e6), "%.0f" % ((dr + dtx) * 8 / dt_s / 1000)


def fix_header(path):
    """옛 열 구성(머리줄이 다름)이면 새 머리줄로 바꾸고 옛 줄은 빈칸으로 채운다."""
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError:
        return
    if not lines or lines[0] == ",".join(COLS):
        return
    n_new = len(COLS); out = [",".join(COLS)]
    for l in lines[1:]:
        out.append(l + "," * max(0, n_new - 1 - l.count(",")))
    open(path + ".tmp", "w", encoding="utf-8").write("\n".join(out) + "\n")
    os.replace(path + ".tmp", path)


def usage(day=None):
    """하루 시간별 사용량·평균/최대 속도 요약(uplink_log.csv)."""
    day = day or dt.datetime.now().strftime("%Y-%m-%d")
    hours = {}
    for r in csv.DictReader(open(OUT, encoding="utf-8")):
        if not r["ts"].startswith(day) or not r.get("kbps"):
            continue
        h = hours.setdefault(r["ts"][11:13], [0.0, 0.0, 0.0, 0])
        h[0] += float(r["rx_mb"]); h[1] += float(r["tx_mb"]); h[2] = max(h[2], float(r["kbps"])); h[3] += 1
    if not hours:
        print("%s 기록 없음" % day); return
    tot = 0.0
    print("%s 시간별 (Pi→Wi-Fi 동글)  받음/보냄 MB · 평균 Mbps · 최대 1분 Mbps" % day)
    for hh in sorted(hours):
        rx, tx, mx, n = hours[hh]; tot += rx + tx
        print("  %s시  %7.1f / %6.1f   평균 %5.2f   최대 %5.2f   (%d분)" % (hh, rx, tx, (rx + tx) * 8 / (n * 60), mx / 1000, n))
    print("합계 %.0f MB" % tot)


def prune(path, keep_days=40):
    try:
        if time.time() - os.path.getmtime(path + ".pruned") < 86400:
            return
    except OSError:
        pass
    cut = (dt.datetime.now() - dt.timedelta(days=keep_days)).strftime("%Y-%m-%d")
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
        keep = lines[:1] + [l for l in lines[1:] if l[:10] >= cut]
        open(path + ".tmp", "w", encoding="utf-8").write("\n".join(keep) + "\n")
        os.replace(path + ".tmp", path)
    except OSError:
        pass
    open(path + ".pruned", "w").close()


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "usage":
        usage(sys.argv[2] if len(sys.argv) > 2 else None); return 0
    now = time.time(); c = counters()
    try:
        prev = json.load(open(BYTES_STATE))
        prev = (prev["ts"], prev["rx"], prev["tx"])
    except Exception:
        prev = None
    cur = (now, c[0], c[1]) if c else None
    if cur:
        json.dump({"ts": now, "rx": cur[1], "tx": cur[2]}, open(BYTES_STATE, "w"))
    w = wifi()
    if not w:
        return 0
    ssid, sig, gw = w
    row = {"ts": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "ssid": ssid, "wifi_sig": sig}
    row["rx_mb"], row["tx_mb"], row["kbps"] = traffic(prev, cur, now)
    if gw == ZTE_GW:
        row.update(zte())
    row["gw_ms"] = ping_ms(gw) if gw else "-1"
    row["inet_code"], row["inet_ms"] = inet()
    fix_header(OUT)
    new = not os.path.exists(OUT)
    with open(OUT, "a", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=COLS)
        if new:
            wr.writeheader()
        wr.writerow({k: row.get(k, "") for k in COLS})
    prune(OUT)
    if "-v" in sys.argv:
        print(row)
    return 0


if __name__ == "__main__":
    sys.exit(main())
