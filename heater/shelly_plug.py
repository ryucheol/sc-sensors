#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shelly Gen2/Gen3 플러그(예: Plug M Gen3) 로컬 드라이버 — tinytuya.OutletDevice 호환 어댑터.

heater_control.py 가 쓰는 tinytuya 인터페이스만 흉내 낸다(제어 로직은 그대로):
  turn_on() / turn_off()
  status() -> {"dps": {"1": 켜짐, "19": W×10, "20": V×10, "17": kWh×100, "9": 남은 카운트다운 초}}
  set_value(9, 초)  : 데드맨 카운트다운 — 초>0 이면 '지금 상태 유지 + 초 뒤 반전'(Switch.Set toggle_after),
                      0 이면 타이머 해제(현재 상태로 Switch.Set, toggle_after 없이)
통신: HTTP JSON-RPC (POST /rpc). 클라우드·키 불필요, 같은 LAN만 있으면 됨.
인증: 플러그에 비밀번호를 걸었으면 SHA-256 Digest(사용자 admin)로 자동 처리.
⚠ 확인 필요(문서 미기재): 타이머가 도는 중 Switch.Set 을 다시 부르면 ① toggle_after 가 있으면 타이머가 새로 시작되는지
   ② 없으면 기존 타이머가 취소되는지. → shelly_check.py --timer-test 로 확인 후 PI_SETUP/HEATER 문서에 기록.
설정 예(tuya_keys.json, gitignore 대상): {"name":"Main heater","type":"shelly","ip":"10.42.0.50","password":""}"""
import json, time, hashlib, os, urllib.request, urllib.error

DP_SWITCH, DP_KWH, DP_WATT, DP_VOLT, DP_COUNTDOWN = "1", "17", "19", "20", "9"


def _h(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def parse_www_auth(header):
    """'Digest qop="auth", realm="x", nonce="1", algorithm=SHA-256' → dict."""
    h = header.strip()
    if h.lower().startswith("digest"):
        h = h[6:]
    out = {}
    for part in h.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip().lower()] = v.strip().strip('"')
    return out


def digest_header(chal, password, method="POST", uri="/rpc", user="admin", cnonce=None, nc="00000001"):
    """Shelly Gen2 Digest(SHA-256) Authorization 헤더 문자열(순수)."""
    cnonce = cnonce or os.urandom(8).hex()
    ha1 = _h("%s:%s:%s" % (user, chal["realm"], password))
    ha2 = _h("%s:%s" % (method, uri))
    resp = _h("%s:%s:%s:%s:%s:%s" % (ha1, chal["nonce"], nc, cnonce, chal.get("qop", "auth"), ha2))
    return ('Digest username="%s", realm="%s", nonce="%s", uri="%s", cnonce="%s", nc=%s, qop=%s, '
            'response="%s", algorithm=SHA-256' % (user, chal["realm"], chal["nonce"], uri, cnonce, nc,
                                                  chal.get("qop", "auth"), resp))


def countdown_remaining(sw, unixtime):
    """Switch 상태의 타이머 → 남은 초. 타이머 없음=0, 시각을 모르면(NTP 없음) None(=보고 없음 취급)."""
    if not sw or sw.get("timer_started_at") is None or sw.get("timer_duration") is None:
        return 0
    if unixtime is None:
        return None
    return max(0, int(round(sw["timer_started_at"] + sw["timer_duration"] - unixtime)))


def to_dps(st):
    """Shelly.GetStatus 결과 → tinytuya 형식 dps(순수). 값 단위는 투야와 같게(W×10, V×10, kWh×100)."""
    sw = st.get("switch:0") or {}
    unixtime = (st.get("sys") or {}).get("unixtime")
    dps = {DP_SWITCH: bool(sw.get("output")),
           DP_WATT: int(round((sw.get("apower") or 0.0) * 10)),
           DP_VOLT: int(round((sw.get("voltage") or 0.0) * 10)),
           DP_COUNTDOWN: countdown_remaining(sw, unixtime)}
    tot = (sw.get("aenergy") or {}).get("total")       # Wh
    if tot is not None:
        dps[DP_KWH] = int(round(tot / 10.0))           # Wh → 0.01kWh
    t = (sw.get("temperature") or {}).get("tC")
    if t is not None:
        dps["temp_c"] = t                              # 참고용(플러그 내부 온도)
    return dps


class ShellyPlug:
    """tinytuya.OutletDevice 흉내. transport(method, params)->result 를 주입하면 테스트 가능."""

    def __init__(self, ip, password="", timeout=5, switch_id=0, transport=None):
        self.ip, self.password, self.timeout, self.sid = ip, password or "", timeout, switch_id
        self._rpc = transport or self._http_rpc
        self._chal = None
        self._nc = 0

    # tinytuya 호환용(무시)
    def set_version(self, *_a, **_k): pass
    def set_socketTimeout(self, t): self.timeout = t

    def _post(self, body, auth=None):
        req = urllib.request.Request("http://%s/rpc" % self.ip, data=json.dumps(body).encode("utf-8"),
                                     method="POST", headers={"Content-Type": "application/json"})
        if auth:
            req.add_header("Authorization", auth)
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def _http_rpc(self, method, params=None):
        body = {"id": 1, "method": method, "params": params or {}}
        for attempt in range(2):
            auth = None
            if self._chal and self.password:
                self._nc += 1
                auth = digest_header(self._chal, self.password, nc="%08x" % self._nc)
            try:
                res = self._post(body, auth)
                break
            except urllib.error.HTTPError as e:
                if e.code == 401 and attempt == 0:
                    if not self.password:
                        raise RuntimeError("Shelly 인증 필요(비밀번호 설정됨) — 키 파일에 password 를 넣으세요")
                    self._chal = parse_www_auth(e.headers.get("WWW-Authenticate", "")); self._nc = 0
                    continue
                raise
        if "error" in res:
            raise RuntimeError("Shelly %s 오류: %s" % (method, res["error"]))
        return res.get("result")

    # ── tinytuya 호환 API ──
    def status(self):
        return {"dps": to_dps(self._rpc("Shelly.GetStatus", {}) or {})}

    def turn_on(self):
        return self._rpc("Switch.Set", {"id": self.sid, "on": True})

    def turn_off(self):
        return self._rpc("Switch.Set", {"id": self.sid, "on": False})

    def set_value(self, dp, value):
        """dp 9 만 지원(데드맨 카운트다운). value>0: 현재 상태 유지 + value초 뒤 반전, 0: 타이머 해제."""
        if str(dp) != DP_COUNTDOWN:
            raise ValueError("ShellyPlug.set_value: DP%s 미지원(9=카운트다운만)" % dp)
        cur = bool((self._rpc("Switch.GetStatus", {"id": self.sid}) or {}).get("output"))
        params = {"id": self.sid, "on": cur}
        if int(value) > 0:
            params["toggle_after"] = int(value)
        return self._rpc("Switch.Set", params)


def make_device(dev):
    """키 파일 항목 → 장치 객체. type=shelly 면 ShellyPlug, 아니면 tinytuya(지연 임포트)."""
    if (dev.get("type") or "tuya").lower() == "shelly":
        return ShellyPlug(dev["ip"], dev.get("password", ""))
    import tinytuya
    d = tinytuya.OutletDevice(dev["id"], dev["ip"], dev["key"])
    d.set_version(float(dev["version"]))
    d.set_socketTimeout(5)
    return d
