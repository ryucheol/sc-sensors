#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""떠날 때 물 동파 점검 알림.

'동파' 명령(= 떠남) 때 앞으로 며칠(기본 7일) 예보 최저가 기준(기본 2°C) 이하이면
배관 물 빼기 목록을 명령 답장에 붙이고, 농막에 있던 사람에게 Home Mini로도 말해 준다.
실내는 히터가 3/5°C로 지키지만 바닥 밑 배관·트랩은 바깥 기온과 거의 같다(바닥외부 센서 ≈ 관측소 +0.5~2°C).

예보: 단기 forecast.json(약 3일, 시간별) + 중기 mid_forecast.json(4~10일, 일 최저, weather/kma_sync.py가 저장).
설정(선택): heater_config.json "depart_check": {"tmin": 2, "days": 7, "items": [...], "voice": true}

CLI(시험): python3 heater/depart_check.py   → 지금 기준 점검 문장 출력(없으면 '해당 없음')
"""
import os, sys, json, time, datetime, subprocess

BASE = os.path.expanduser("~/smart-cabin")
FORECAST = os.path.join(BASE, "forecast.json")
MID = os.path.join(BASE, "mid_forecast.json")
CFG = os.path.join(BASE, "heater_config.json")
REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))

DEFAULT_ITEMS = ["P트랩 아래 마개 풀기(배수구는 막기)", "급수 밸브 잠그고 꼭지 열어 물 빼기", "변기 물탱크 비우기"]


def _fnum(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def forecast_min(now_ts, series, mid_rows, days=7):
    """앞으로 days일 예보 최저 → (기온, 'MM-DD HH시' 또는 'MM-DD', 출처) 또는 (None, None, None). 순수 함수.
       series: [(epoch초, 기온)] 시간별 단기예보. mid_rows: [[YYYY-MM-DD, …, 최저(5번째 칸), …]] 중기예보."""
    end = now_ts + days * 86400
    best = (None, None, None)
    for ts, t in series or []:
        if now_ts - 3600 <= ts <= end and (best[0] is None or t < best[0]):
            best = (float(t), time.strftime("%m-%d %H시", time.localtime(ts)), "단기")
    last_short = max((ts for ts, _ in series), default=now_ts) if series else now_ts
    for r in mid_rows or []:
        try:
            d = datetime.datetime.strptime(str(r[0])[:10], "%Y-%m-%d")
        except (ValueError, IndexError):
            continue
        day_ts = time.mktime(d.timetuple())
        if day_ts + 86400 <= last_short or day_ts > end:   # 단기예보가 덮는 날은 단기 값을 쓴다
            continue
        t = _fnum(r[5]) if len(r) > 5 else None
        if t is not None and (best[0] is None or t < best[0]):
            best = (t, d.strftime("%m-%d"), "중기")
    return best


def message(tmin, when, threshold=2.0, items=None):
    """예보 최저가 기준 이하이면 알림 문장, 아니면 None. 순수 함수."""
    if tmin is None or tmin > threshold:
        return None
    items = items or DEFAULT_ITEMS
    return ("🚰 물 동파 점검: 앞으로 최저 %g°C(%s) — 바닥 밑 배관은 바깥과 같습니다.\n" % (tmin, when)
            + "\n".join("• " + x for x in items))


def speech(tmin, items=None):
    items = items or DEFAULT_ITEMS
    return "떠나시기 전에 물을 빼 주세요. 최저 %s도 예보입니다. %s." % (
        ("영하 %g" % -tmin) if tmin < 0 else "%g" % tmin, ", ".join(x.split("(")[0] for x in items))


def _load(path, default):
    try:
        return json.load(open(path))
    except Exception:
        return default


def check(now_ts=None):
    """지금 기준 (알림 문장 또는 None, 음성 문장 또는 None, 설정)."""
    now_ts = now_ts or time.time()
    cfg = (_load(CFG, {}) or {}).get("depart_check", {})
    sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
    try:
        import heater_control as hc
        series = hc.forecast_series(FORECAST)
    except Exception:
        series = []
    mid = (_load(MID, {}) or {}).get("rows", [])
    tmin, when, _src = forecast_min(now_ts, series, mid, cfg.get("days", 7))
    items = cfg.get("items") or DEFAULT_ITEMS
    msg = message(tmin, when, cfg.get("tmin", 2.0), items)
    return msg, (speech(tmin, items) if msg else None), cfg


def announce(text):
    """Home Mini(실패하면 Pi 스피커)로 말하기 — 백그라운드, 실패해도 조용히."""
    script = os.path.join(REPO, "alert", "alert_sound.py")
    if not os.path.exists(script):
        script = os.path.join(REPO, "alert_sound.py")
    try:
        subprocess.Popen([sys.executable or "python3", script, "say", text], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


if __name__ == "__main__":
    m, s, _ = check()
    print(m or "해당 없음(예보 최저가 기준보다 높음)")
    if s and len(sys.argv) > 1 and sys.argv[1] == "say":
        announce(s)
