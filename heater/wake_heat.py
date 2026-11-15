#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""아침 난방 예약 — cron 매분. 정한 시각에 '취침' 모드면 '난방 T'로 바꾼다(1층을 일어나기 전에 데움, 선풍기 자동).

취침이 아니면(이미 난방·동파·떠남) 아무것도 안 한다 → 무인일 땐 절대 켜지 않음.
설정 파일 wake.json(gitignore): {"enabled": true, "time": "06:00", "target": null(=기본 난방), "done": "2026-11-16"}
명령(텔레그램·Pi 터미널): '아침'(상태) / '아침 06:30' / '아침 06:30 24' / '아침 끔' / '아침 켬'
실행: python3 heater/wake_heat.py [--dry-run]   테스트: cd heater && python3 -m unittest test_wake_heat
"""
import os, sys, json, time, subprocess, datetime

HERE = os.path.dirname(os.path.realpath(__file__))
BASE = os.path.dirname(HERE)
WAKE = os.path.join(BASE, "wake.json")
STATE = os.path.join(BASE, "heater_state.json")
LOG = os.path.join(BASE, "wake.log")
DEFAULT = {"enabled": True, "time": "06:00", "target": None}   # target None = 기본 난방(heater_config comfort)


def load_wake():
    w = dict(DEFAULT)
    try:
        w.update(json.load(open(WAKE, encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return w


def save_wake(w):
    with open(WAKE + ".tmp", "w", encoding="utf-8") as f:
        json.dump(w, f, ensure_ascii=False)
    os.replace(WAKE + ".tmp", WAKE)


def due(w, mode, now):
    """지금 바꿀지(순수): 켜져 있고, 취침 모드이고, 오늘 아직 안 했고, 정한 시각~30분 안."""
    if not w.get("enabled") or mode != "sleep":
        return False
    if w.get("done") == now.strftime("%Y-%m-%d"):
        return False
    hh, mm = (int(x) for x in w["time"].split(":"))
    t = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return t <= now < t + datetime.timedelta(minutes=30)   # cron이 한두 번 빠져도 30분 안이면 실행


def parse_cmd(toks, w):
    """'아침' 인자 → (새 설정, 안내문)(순수)."""
    import re
    w = dict(w)
    txt = " ".join(toks).strip()
    if not txt:
        return w, None
    if txt in ("끔", "꺼", "끄기", "off", "해제"):
        w["enabled"] = False; return w, "아침 난방 예약 끔"
    if txt in ("켬", "켜", "켜기", "on"):
        w["enabled"] = True; return w, "아침 난방 예약 켬"
    m = re.search(r"(\d{1,2}):(\d{2})", txt) or re.search(r"(\d{1,2})시(?:\s*(\d{1,2})분)?", txt)
    if not m:
        raise ValueError("사용: 아침 06:30 [목표°C] / 아침 끔 / 아침 켬")
    hh, mm = int(m.group(1)), int(m.group(2) or 0)
    if not (0 <= hh < 24 and 0 <= mm < 60):
        raise ValueError("시각이 이상함")
    w["time"] = "%02d:%02d" % (hh, mm); w["enabled"] = True
    rest = txt[:m.start()] + " " + txt[m.end():]
    t = re.search(r"(\d{2}(?:\.\d)?)\s*도?", rest)
    if t and 15 <= float(t.group(1)) <= 28:
        w["target"] = float(t.group(1))
    else:
        w["target"] = None   # 온도를 안 주면 기본 난방
    return w, "아침 난방 예약: 매일 %s, 취침 모드일 때만 %s" % (w["time"], _tgt(w))


def _tgt(w):
    if w.get("target"):
        return "난방 %g°C로" % w["target"]
    try:
        c = json.load(open(os.path.join(BASE, "heater_config.json")))["comfort"]
        return "기본 난방(%g/%g°C)으로" % (c["on"], c["off"])
    except Exception:
        return "기본 난방으로"


def describe(w):
    return "아침 난방 예약 %s — 매일 %s에 취침 모드이면 %s (명령: 아침 06:30 [온도] / 아침 끔)" % (
        "켬" if w.get("enabled") else "꺼짐", w["time"], _tgt(w))


def main(argv):
    w = load_wake()
    try:
        mode = json.load(open(STATE)).get("mode")
    except Exception:
        mode = None
    now = datetime.datetime.now()
    if not due(w, mode, now):
        return 0
    if "--dry-run" in argv:
        print("due: 난방", w["target"]); return 0
    r = subprocess.run([sys.executable, os.path.join(HERE, "heater_cmd.py"), "난방"] + (["%g" % w["target"]] if w.get("target") else []),
                       capture_output=True, text=True, timeout=60, cwd=BASE)
    w["done"] = now.strftime("%Y-%m-%d"); save_wake(w)
    line = "[%s] 아침 난방 예약 실행: %s → %s" % (now.strftime("%Y-%m-%d %H:%M:%S"), _tgt(w), (r.stdout + r.stderr).strip()[:200])
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    try:
        sys.path.insert(0, os.path.join(BASE, "notify"))
        import telegram_notify as tg
        tg.send("☀️ 아침 난방 예약: 취침 → %s (선풍기 자동)" % _tgt(w), queue=False)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
