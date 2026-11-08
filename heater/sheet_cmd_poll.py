#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""구글 시트 '명령' 탭 폴링 → 히터 명령 실행 → 결과 기록. cron 매분 실행.

시트(Apps Script v9) '명령' 탭: A 명령(사람이 입력) · B 상태 · C 처리시각 · D 결과 · E 입력시각(onEdit 자동).
이 스크립트는 mode:cmd_pull 로 B가 빈 행을 받아 heater_cmd.py 로 실행하고 mode:cmd_ack 로 B~D를 채운다.
허용 명령(첫 토큰): 난방 동파 취침 도착 상태 점검 (영문 heat/frost/sleep/arrive/status/check).
입력 후 CMD_TTL_H 시간이 지난 행은 실행하지 않고 '만료'로 표시(Pi가 오래 오프라인이었던 경우 보호).
설정: gateway.env 의 GW_WEBAPP_URL(없으면 게이트웨이 기본 URL)·FARMHUT_SECRET.
"""
import os, sys, json, time, subprocess, urllib.request, socket, datetime

HERE = os.path.dirname(os.path.realpath(__file__))
BASE = os.path.dirname(HERE)
LOG = os.path.join(BASE, "sheet_cmd.log")
CMD_TTL_H = 12.0
PY = sys.executable or "/usr/bin/python3"
VENV_PY = os.path.expanduser("~/tuya-venv/bin/python")

ALIASES = {
    "난방": "난방", "heat": "난방", "comfort": "난방",
    "동파": "동파", "frost": "동파",
    "취침": "취침", "sleep": "취침",
    "도착": "도착", "arrive": "도착", "arrival": "도착",
    "상태": "상태", "status": "상태",
    "점검": "점검", "check": "점검", "히터점검": "점검",
}


def log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        with open(LOG, "a", encoding="utf-8") as f: f.write(line + "\n")
    except Exception: pass


def load_env():
    env = {}
    try:
        for ln in open(os.path.join(BASE, "gateway.env"), encoding="utf-8"):
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln: continue
            k, v = ln.split("=", 1); env[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError: pass
    url = env.get("GW_WEBAPP_URL")
    if not url:   # 게이트웨이 소스의 기본 URL을 그대로 쓴다
        import re
        src = open(os.path.join(BASE, "gateway", "passive_gateway.py"), encoding="utf-8").read()
        m = re.search(r'"(https://script\.google\.com/macros/s/[^"]+/exec)"', src)
        url = m.group(1) if m else None
    return url, env.get("FARMHUT_SECRET", "CHANGE-ME")


def post(url, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def online():
    try:
        socket.create_connection(("script.google.com", 443), timeout=4).close(); return True
    except OSError: return False


def status_text():
    """현재 상태 한 줄(상태 명령·각 명령 결과 뒤에 붙임)."""
    try: s = json.load(open(os.path.join(BASE, "heater_status.json")))
    except Exception: s = {}
    try: st = json.load(open(os.path.join(BASE, "heater_state.json")))
    except Exception: st = {}
    mode = {"comfort": "난방", "frost": "동파", "sleep": "취침"}.get(st.get("mode"), st.get("mode", "?"))
    parts = ["%s ON%g/OFF%g" % (mode, st.get("on", 0), st.get("off", 0))]
    if s.get("t_ctl") is not None: parts.append("%s %.1f°C" % (s.get("ctl_sensor", "실내"), s["t_ctl"]))
    rel = "히터 %s" % ("ON" if s.get("relay") else "OFF")
    if s.get("stage"): rel += "(%d단 %sW)" % (s["stage"], s.get("stage_w"))
    parts.append(rel)
    if s.get("today_kwh") is not None: parts.append("오늘 %.1fkWh" % s["today_kwh"])
    if s.get("arrival"):
        try:
            a = s["arrival"]; ts = a.get("ts") if isinstance(a, dict) else None
            parts.append("도착예약 %s" % (datetime.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M") if ts else a))
            if s.get("arrival_start_est"):
                parts.append("예상시작 %s" % datetime.datetime.fromtimestamp(s["arrival_start_est"]).strftime("%m-%d %H:%M"))
        except Exception: pass
    if s.get("updated"):
        try:
            age = (time.time() - float(s["updated"])) / 60.0
            if age > 5: parts.append("⚠ 제어기 상태 %.0f분 전" % age)
        except Exception: pass
    return " · ".join(parts)


def run(cmd_text):
    """시트 한 행의 명령 문자열 실행 → (status, result)."""
    toks = [t.rstrip("에,.") for t in cmd_text.replace("　", " ").split() if t.strip()]
    verb = None
    for i, t in enumerate(toks):
        if t.lower() in ALIASES: verb = ALIASES[t.lower()]; toks.pop(i); break
    if not verb:
        return "오류", "알 수 없는 명령 — 난방 / 동파 / 취침 / 도착 HH:MM [내일|날짜] [목표°C] / 도착 취소 / 상태 / 점검"
    if verb == "상태":
        return "완료", status_text()
    if verb == "점검":
        if not os.path.exists(VENV_PY): return "오류", "tuya-venv 없음"
        r = subprocess.run([VENV_PY, os.path.join(HERE, "heater_selftest.py")], capture_output=True, text=True,
                           timeout=180, env=dict(os.environ, PYTHONIOENCODING="utf-8"), cwd=BASE)
        out = (r.stdout + r.stderr).strip()
        return ("완료" if r.returncode == 0 else "오류"), out[-800:]
    r = subprocess.run([PY, os.path.join(HERE, "heater_cmd.py"), verb] + toks, capture_output=True, text=True,
                       timeout=60, env=dict(os.environ, PYTHONIOENCODING="utf-8"), cwd=BASE)
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0: return "오류", out[-600:]
    return "완료", (out + "\n" + status_text())[-800:]


def main():
    url, secret = load_env()
    if not url: log("WEBAPP_URL 없음"); return
    if not online(): return
    try: res = post(url, {"secret": secret, "mode": "cmd_pull"})
    except Exception as e: log("pull 실패: %s" % e); return
    if not res.get("ok"): log("pull 오류: %s" % res); return
    if res.get("cal_error"): log("캘린더 오류(시트): %s" % res["cal_error"])
    cmds = res.get("commands") or []
    if not cmds: return
    acks = []
    for c in cmds:
        row, text = c.get("row"), str(c.get("cmd", "")).strip()
        entered = c.get("entered_ms")
        try: age_h = (time.time() * 1000 - float(entered)) / 3.6e6 if entered else 0.0
        except Exception: age_h = 0.0
        if age_h > CMD_TTL_H:
            status, result = "만료", "입력 후 %.0f시간 지나 실행하지 않음(Pi 오프라인?) — 필요하면 다시 입력" % age_h
        else:
            try: status, result = run(text)
            except subprocess.TimeoutExpired: status, result = "오류", "시간 초과"
            except Exception as e: status, result = "오류", str(e)
        log("row %s %r → %s: %s" % (row, text, status, result.replace("\n", " | ")[:200]))
        acks.append({"row": row, "status": status, "result": result, "done_ms": int(time.time() * 1000)})
    try:
        r2 = post(url, {"secret": secret, "mode": "cmd_ack", "acks": acks})
        if not r2.get("ok"): log("ack 오류: %s" % r2)
    except Exception as e: log("ack 실패: %s" % e)


if __name__ == "__main__":
    import fcntl
    _lk = open(os.path.join(BASE, ".sheet_cmd.lock"), "w")
    try: fcntl.flock(_lk, fcntl.LOCK_EX | fcntl.LOCK_NB)   # cron 겹침·수동 실행 중복 방지
    except OSError: sys.exit(0)
    main()
