# -*- coding: utf-8 -*-
"""농막 TV 대시보드 — 읽기 전용 로컬 웹 서버.

TV 브라우저에서 http://10.42.0.1:8080 을 열면 히터·온도·예보를 큰 글씨로 보여준다.
- 표준 라이브러리만 사용(외부 CDN 없음 → 회선이 끊겨도 LAN에서 동작).
- 명령은 받지 않는다(GET만). 농막 LAN(10.42.0.0/24)·Tailscale·로컬에서만 응답.
- /api/status : 상태 JSON(30초마다 페이지가 가져감)
"""
import ipaddress, json, os, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # ~/smart-cabin
HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("TV_DASH_PORT", "8080"))
ALLOW = [ipaddress.ip_network(n) for n in ("10.42.0.0/24", "100.64.0.0/10", "127.0.0.0/8", "fd7a:115c:a1e0::/48", "::1/128")]
SERIES_H = 24          # 그래프 시간
SERIES_STEP_MIN = 5    # 그래프 간격


def jload(name, default=None):
    try:
        with open(os.path.join(BASE, name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def tail_lines(path, max_bytes):
    """파일 끝에서 max_bytes 안의 완전한 줄들(순수 I/O)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2); size = f.tell()
            f.seek(max(0, size - max_bytes))
            data = f.read().decode("utf-8", "replace")
    except Exception:
        return []
    lines = data.splitlines()
    return lines[1:] if size > max_bytes else lines[1:]   # 첫 줄: 잘린 줄 또는 머리글


def fnum(s):
    try:
        return float(s)
    except Exception:
        return None


def series(now_ts):
    """thermal_log.csv 최근 SERIES_H시간을 SERIES_STEP_MIN분 간격으로: [epoch, 1층, 다락, 바깥, relay, watt]."""
    # 1분 1줄 ≈ 90바이트 → 24h ≈ 130KB. 넉넉히 250KB
    rows = tail_lines(os.path.join(BASE, "thermal_log.csv"), 250_000)
    t0 = now_ts - SERIES_H * 3600
    out = []; last_bucket = None; on_acc = []
    for ln in rows:
        p = ln.split(",")
        if len(p) < 11: continue
        try:
            ts = time.mktime(time.strptime(p[0], "%Y-%m-%d %H:%M:%S"))
        except Exception:
            continue
        if ts < t0: continue
        b = int(ts // (SERIES_STEP_MIN * 60))
        on_acc.append(1 if p[2] == "1" else 0)
        if b == last_bucket: continue
        last_bucket = b
        out.append([int(ts), fnum(p[5]), fnum(p[7]), fnum(p[9]), max(on_acc) if on_acc else 0, fnum(p[3])])
        on_acc = []
    return out


def status():
    now_ts = time.time()
    hs = jload("heater_status.json", {}) or {}
    st = jload("heater_state.json", {}) or {}
    fc = jload("forecast.json", {}) or {}
    hourly = [h for h in (fc.get("hourly") or []) if h.get("ms", 0) / 1000.0 >= now_ts - 3600][:13]
    keep = ("updated", "mode", "relay", "watt", "today_kwh", "t_ctl", "ctl_sensor", "stage", "stage_w",
            "delta_attic_floor", "t_mass", "arrival", "arrival_start_est", "arrival_start_pred",
            "arrival_solar_start", "manual", "advice", "heater_ok")
    return {
        "now": int(now_ts),
        "heater": {k: hs.get(k) for k in keep},
        "state": {k: st.get(k) for k in ("mode", "on", "off", "source", "since")},
        "sensors": jload("latest.json", {}) or {},
        "qingping": jload("qingping_latest.json", {}) or {},
        "fan": jload("fan_status.json", {}) or {},
        "aux": jload("aux_status.json", {}) or {},
        "forecast": {"hourly": hourly, "daily": fc.get("daily") or {}, "base": fc.get("base")},
        "series": series(now_ts),
    }


class H(BaseHTTPRequestHandler):
    server_version = "tvdash/1"

    def log_message(self, *a):
        pass

    def _allowed(self):
        try:
            ip = ipaddress.ip_address(self.client_address[0].split("%")[0])
            if ip.version == 6 and ip.ipv4_mapped: ip = ip.ipv4_mapped
            return any(ip in n for n in ALLOW)
        except Exception:
            return False

    def _send(self, code, body, ctype):
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if not self._allowed():
            return self._send(403, "forbidden", "text/plain; charset=utf-8")
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            try:
                with open(os.path.join(HERE, "tv.html"), encoding="utf-8") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            except Exception as e:
                return self._send(500, "tv.html: %s" % e, "text/plain; charset=utf-8")
        if path == "/api/beacon":      # 화면 진단(크기·넘침) 기록 — 최근 50줄만
            try:
                q = self.path.split("?", 1)[1] if "?" in self.path else ""
                from urllib.parse import unquote
                ln = "%s %s %s\n" % (time.strftime("%m-%d %H:%M:%S"), self.client_address[0], unquote(q)[:1500])
                fn = os.path.join(BASE, "tv_dash_beacon.log")
                old = open(fn, encoding="utf-8").readlines()[-49:] if os.path.exists(fn) else []
                open(fn, "w", encoding="utf-8").writelines(old + [ln])
            except Exception:
                pass
            return self._send(204, b"", "text/plain")
        if path == "/api/status":
            try:
                return self._send(200, json.dumps(status(), ensure_ascii=False), "application/json; charset=utf-8")
            except Exception as e:
                return self._send(500, json.dumps({"error": str(e)}), "application/json")
        return self._send(404, "not found", "text/plain; charset=utf-8")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--dump":
        print(json.dumps(status(), ensure_ascii=False)[:2000]); sys.exit(0)
    ThreadingHTTPServer.allow_reuse_address = True
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), H)
    srv.serve_forever()
