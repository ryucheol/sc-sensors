#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""구글 시트 '명령' 탭 폴링 → 히터 명령 실행 → 결과 기록. cron 매분 실행하되 실제 확인은 매시 7분(POLL_MINUTE, LTE 절약).

시트(Apps Script v9) '명령' 탭: A 명령(사람이 입력) · B 상태 · C 처리시각 · D 결과 · E 입력시각(onEdit 자동).
이 스크립트는 mode:cmd_pull 로 B가 빈 행을 받아 heater_cmd.py 로 실행하고 mode:cmd_ack 로 B~D를 채운다.
허용 명령(첫 토큰): 난방 동파 취침 도착 상태 점검 기상 (영문 heat/frost/sleep/arrive/status/check/weather).
'기상'은 weather/kma_sync.py --force 를 백그라운드로 띄운다(기상청 → 시트 즉시 갱신).
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
    "상태": "상태", "status": "상태", "현황": "상태",
    "점검": "점검", "check": "점검", "히터점검": "점검",
    "기상": "기상", "weather": "기상", "날씨": "기상",
    "선풍기": "선풍기", "fan": "선풍기", "팬": "선풍기",
    "공유": "공유", "share": "공유", "인터넷": "공유",
    "센서": "센서", "온도": "센서", "습도": "센서", "sensors": "센서", "sensor": "센서",
    "보조": "보조", "보조히터": "보조", "aux": "보조",
    "아침": "아침", "wake": "아침",
    "시보": "시보", "chime": "시보",
}
CHIME_FILE = os.path.join(BASE, "chime.json")   # alert_sound.py 시보 켜짐/꺼짐(파일 없으면 켜짐)


def chime_cmd(toks, path=None):
    """시보 / 시보 켜 / 시보 끔 → (status, 문구)."""
    path = path or CHIME_FILE
    try: on = bool(json.load(open(path)).get("on", True))
    except Exception: on = True
    a = toks[0] if toks else ""
    if a in ("켜", "켜기", "켬", "on", "ON"): new = True
    elif a in ("꺼", "끔", "끄기", "off", "OFF"): new = False
    elif a in ("", "상태"): new = None
    else: return "오류", "시보 / 시보 켜 / 시보 끔"
    if new is not None:
        with open(path + ".tmp", "w") as f: json.dump({"on": new, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}, f)
        os.replace(path + ".tmp", path); on = new
    return "완료", "시보 %s — 난방 모드 07~22시 정각·30분에 Home Mini로 시각·실내온도%s" % (
        "켜짐" if on else "꺼짐", "" if on else " (지금은 말하지 않음, 경보는 그대로)")


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


def preheat_started(st):
    """도착 예열이 이미 시작됐으면 시작 epoch, 아니면 None. 제어기가 예열을 켤 때 mode=comfort, source=arrival, since=시각."""
    if (st or {}).get("mode") == "comfort" and st.get("source") in ("arrival", "preheat") and st.get("since"):
        return st["since"]
    return None


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
    try:
        fs = json.load(open(os.path.join(BASE, "fan_status.json")))
        parts.append("선풍기 %s" % ("ON" if fs.get("on") else "OFF"))
    except Exception: pass
    try:
        as_ = json.load(open(os.path.join(BASE, "aux_status.json")))
        if as_.get("on"): parts.append("보조히터 ON")
    except Exception: pass
    if s.get("arrival"):
        try:
            a = s["arrival"]; ts = a.get("ts") if isinstance(a, dict) else None
            parts.append("도착예약 %s" % (datetime.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M") if ts else a))
            started = preheat_started(st)
            if started:   # 예열이 이미 시작됨 → 예고 대신 실제 시작 시각
                parts.append("예열 중(%s 시작)" % datetime.datetime.fromtimestamp(started).strftime("%m-%d %H:%M"))
            elif s.get("arrival_start_pred"):   # 실내가 예보대로 식는 것까지 반영한 예고(표시용)
                parts.append("예상시작 %s(그때 실내 약 %s°C, 선행 %.1fh)" % (
                    datetime.datetime.fromtimestamp(s["arrival_start_pred"]).strftime("%m-%d %H:%M"),
                    s.get("arrival_tin_pred"), s.get("arrival_lead_pred_h") or 0))
            elif s.get("arrival_start_est"):
                parts.append("예상시작 %s" % datetime.datetime.fromtimestamp(s["arrival_start_est"]).strftime("%m-%d %H:%M"))
        except Exception: pass
    if s.get("updated"):
        try:
            age = (time.time() - datetime.datetime.strptime(s["updated"], "%Y-%m-%d %H:%M:%S").timestamp()) / 60.0
            if age > 5: parts.append("⚠ 제어기 상태 %.0f분 전" % age)
        except Exception: pass
    return " · ".join(parts)


FAN_WORDS = {"켜": "on", "켜기": "on", "켬": "on", "on": "on", "꺼": "off", "끄기": "off", "끔": "off", "off": "off",
             "자동": "auto", "auto": "auto"}


def aux_cmd(toks):
    """보조 켜|꺼|자동 → aux_cmd.json (aux_control.py가 매분 반영). 켜/꺼는 2시간 뒤 자동. 동파 위험(1층 2.5°C 이하)은 '꺼'보다 우선."""
    m = next((FAN_WORDS[t.lower()] for t in toks if t.lower() in FAN_WORDS), None)
    if not m:
        try: st = json.load(open(os.path.join(BASE, "aux_status.json")))
        except Exception: st = {}
        return "완료", "보조히터 %s — %s (명령: 보조 켜|꺼|자동)" % ("ON" if st.get("on") else "OFF", st.get("why", "?"))
    json.dump({"mode": m, "ts": time.time()}, open(os.path.join(BASE, "aux_cmd.json"), "w"))
    msg = {"on": "보조히터 켜기(2시간 뒤 자동으로 돌아감)", "off": "보조히터 끄기(2시간, 단 동파 위험이면 켬)", "auto": "보조히터 자동"}[m]
    return "완료", msg + " — 반영 최대 1분"


def fan_cmd(toks):
    """선풍기 켜|꺼|자동 → fan_cmd.json (fan_control.py가 매분 반영). 켜/꺼는 일정 시간 뒤 자동으로 돌아감."""
    m = next((FAN_WORDS[t.lower()] for t in toks if t.lower() in FAN_WORDS), None)
    if not m:
        try: fs = json.load(open(os.path.join(BASE, "fan_status.json")))
        except Exception: fs = {}
        return "완료", "선풍기 %s — %s (명령: 선풍기 켜|꺼|자동)" % ("ON" if fs.get("on") else "OFF", fs.get("why", "?"))
    json.dump({"mode": m, "ts": time.time()}, open(os.path.join(BASE, "fan_cmd.json"), "w"))
    hold = ((json.load(open(os.path.join(BASE, "heater_config.json"))).get("fan") or {}).get("manual_hold_h", 4)
            if os.path.exists(os.path.join(BASE, "heater_config.json")) else 4)
    msg = {"on": "선풍기 켜기", "off": "선풍기 끄기", "auto": "선풍기 자동"}[m]
    if m != "auto": msg += "(%s시간 뒤 자동으로 돌아감)" % hold
    return "완료", msg + " — 반영 최대 1분"


def sensors_text(latest=None, now=None):
    """모든 센서의 온도·습도·배터리·수신 경과(분) 한 줄씩 + 다락−1층 차(순수에 가깝게: latest·now 주입 가능)."""
    if latest is None:
        try: latest = json.load(open(os.path.join(BASE, "latest.json")))
        except Exception: latest = {}
    now = time.time() if now is None else now
    order = ["내부1층", "다락", "바닥외부"]
    names = order + sorted(k for k in latest if k not in order)
    lines = []
    for k in names:
        v = latest.get(k)
        if not v: continue
        age = (now - v.get("time_ms", 0) / 1000.0) / 60.0 if v.get("time_ms") else None
        s = "%s %s°C %s%%" % (k, "?" if v.get("temp") is None else "%.1f" % v["temp"],
                              "?" if v.get("hum") is None else "%.0f" % v["hum"])
        extra = []
        if age is not None: extra.append("%d분 전" % round(age) if age >= 1 else "방금")
        if v.get("batt") is not None: extra.append("배터리 %s%%" % v["batt"])
        if extra: s += " (" + ", ".join(extra) + ")"
        if age is not None and age > 45: s += " ⚠ 수신 끊김?"
        lines.append(s)
    f, a = (latest.get("내부1층") or {}).get("temp"), (latest.get("다락") or {}).get("temp")
    if f is not None and a is not None: lines.append("다락−1층 %+.1f°C" % (a - f))
    return "\n".join(lines) if lines else "센서 값 없음(latest.json)"


def uplink_text():
    """지금 Pi의 인터넷 경로(기본 경로 장치) 한 줄."""
    try:
        r = subprocess.run(["ip", "route", "get", "1.1.1.1"], capture_output=True, text=True, timeout=5).stdout
        dev = r.split(" dev ")[1].split()[0]
    except Exception:
        return "알 수 없음"
    if dev == "eth1":
        return "LTE 동글(6GB) — Ryufarm 기기는 차단·한도 적용"
    if dev.startswith("wlan"):
        try:
            ssid = subprocess.run(["nmcli", "-g", "GENERAL.CONNECTION", "device", "show", dev],
                                  capture_output=True, text=True, timeout=5).stdout.strip()
        except Exception:
            ssid = "?"
        return "Wi-Fi %s(%s) — 차단·한도 없이 모두 사용" % (ssid, dev)
    return dev


WAN_GUARD = "/usr/local/sbin/farmhut-wan-guard"


def share_parse(toks):
    """공유 인자 → ("open", 초, MB|None) | ("apply", None, None) | ("status", None, None) (순수).
    시간 기본 1시간·최대 24시간, 용량은 '300MB'·'1기가'·'1GB' (없으면 스크립트 기본 100MB, 최대 2048)."""
    import re
    txt = "".join(toks).lower()
    if not txt: return ("open", 3600, None)
    if txt in ("끔", "꺼", "끄기", "차단", "off", "닫기", "막기"): return ("apply", None, None)
    if txt in ("상태", "status", "?"): return ("status", None, None)
    mb = None
    for n, u in re.findall(r"(\d+(?:\.\d+)?)\s*(mb|메가|gb|기가|g)(?![a-z가-힣])", txt):
        mb = (mb or 0) + float(n) * (1024 if u in ("gb", "기가", "g") else 1)
    txt2 = re.sub(r"(\d+(?:\.\d+)?)\s*(mb|메가|gb|기가|g)(?![a-z가-힣])", "", txt)
    sec = 0
    for n, u in re.findall(r"(\d+(?:\.\d+)?)\s*(시간|h|분|m)", txt2):
        sec += float(n) * (3600 if u in ("시간", "h") else 60)
    if not sec and re.fullmatch(r"\d+(?:\.\d+)?", txt2): sec = float(txt2) * 3600
    if not sec and mb is None: return ("status", None, None)
    if not sec: sec = 3600
    if mb is not None: mb = int(min(max(mb, 10), 2048))
    return ("open", int(min(max(sec, 60), 86400)), mb)


def share_cmd(toks):
    if not os.path.exists(WAN_GUARD): return "오류", "farmhut-wan-guard 미설치(sudo bash scripts/install_wan_guard.sh)"
    act, sec, mb = share_parse(toks)
    args = ["sudo", "-n", WAN_GUARD, act] + ([str(sec)] if sec else []) + ([str(mb)] if (act == "open" and mb) else [])
    r = subprocess.run(args, capture_output=True, text=True, timeout=20)
    out = (r.stdout + r.stderr).strip()
    if act == "status":
        out = out.replace(" · ", "\n")
        out = "인터넷 경로: %s\n" % uplink_text() + out
        try:
            du = json.load(open(os.path.join(BASE, "data_usage.json")))
            out += "\n────\nLTE 동글 전체 이번 달 %s / %sGB (%.1f%%, 최근 1시간 %s, %s 기준)" % (
                du.get("used"), du.get("limit_gb"), du.get("pct", 0), du.get("last_hour"), str(du.get("updated", ""))[11:16])
        except Exception:
            pass
    if act == "open" and r.returncode == 0:
        out += " — 넘거나 시간이 끝나면 자동 차단. 먼저 막으려면 '공유 끔', 더 필요하면 '공유 1시간 1기가'"
    return ("완료" if r.returncode == 0 else "오류"), out[-500:]


def run(cmd_text):
    """시트 한 행의 명령 문자열 실행 → (status, result)."""
    toks = [t.rstrip("에,.") for t in cmd_text.replace("　", " ").split() if t.strip()]
    verb = None
    for i, t in enumerate(toks):
        if t.lower() in ALIASES: verb = ALIASES[t.lower()]; toks.pop(i); break
    if not verb:
        return "오류", "알 수 없는 명령 — 난방 / 동파 / 취침 / 도착 HH:MM [내일|날짜] [목표°C] / 도착 취소 / 상태 / 점검 / 기상 / 선풍기 켜|꺼|자동 / 공유 [N시간|N분|끔] / 센서 / 보조 켜|꺼|자동 / 아침 06:30 / 시보 켜|끔"
    if verb == "상태":
        return "완료", status_text()
    if verb == "선풍기":
        return fan_cmd(toks)
    if verb == "공유":
        return share_cmd(toks)
    if verb == "센서":
        return "완료", sensors_text()
    if verb == "보조":
        return aux_cmd(toks)
    if verb == "시보":
        return chime_cmd(toks)
    if verb == "아침":
        import wake_heat as wh
        w = wh.load_wake()
        try: w2, msg = wh.parse_cmd(toks, w)
        except ValueError as e: return "오류", str(e)
        if msg is None: return "완료", wh.describe(w)
        wh.save_wake(w2); return "완료", msg
    if verb == "기상":   # 기상청 → 시트 즉시 갱신. 최대 몇 분 걸리므로 백그라운드로 띄우고 바로 응답
        sync = os.path.join(BASE, "weather", "kma_sync.py")
        if not os.path.exists(sync): return "오류", "weather/kma_sync.py 없음(git pull 필요)"
        subprocess.Popen([PY, sync, "--force"], cwd=BASE, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        return "완료", "기상 갱신 시작 — 1~3분 뒤 안내!A14·대시보드 확인 (기록: kma_sync.log)"
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


POLL_MINUTE = 7   # 시트 명령·캘린더 도착은 매시 7분에 한 번만 확인(LTE 절약 — 급한 명령은 텔레그램이 즉시, 사용자 11-16 결정)


def due_now(mode, minute):
    """이번 분에 시트를 확인할지(순수). 모드와 무관하게 매시 POLL_MINUTE분."""
    return minute == POLL_MINUTE


def sheet_allowed(text):
    """시트 명령 탭은 캘린더 도착 중계 전용(사용자 11-16 결정) — 도착/도착 취소만 실행(순수)."""
    t = text.replace("\u3000", " ").split()
    return bool(t) and ALIASES.get(t[0].lower()) == "도착"


def main():
    try: _mode = json.load(open(os.path.join(BASE, "heater_state.json"))).get("mode")
    except Exception: _mode = None
    if not due_now(_mode, time.localtime().tm_min): return
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
        elif not sheet_allowed(text):
            status, result = "무시", "시트는 캘린더 도착 중계만 함(도착·도착 취소). 다른 명령은 텔레그램 봇으로"
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
