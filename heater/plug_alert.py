#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""플러그 무응답 알림(순수 판단) — 보조 히터·선풍기 제어(cron 매분)가 매번 결과를 넘긴다.

11-16~11-20: 보조 히터 플러그가 4일 동안 응답이 없었는데(뽑힌 듯) 아무도 몰랐다. 보조 히터는 동파 백업이기도 하다.
  연속 FIRST_MIN분 응답이 없으면 한 번 알림, 계속되면 REMIND_H시간마다 다시, 돌아오면 '복구' 한 번.
"""
FIRST_MIN = 30
REMIND_H = 24


def update(st, ok, now, name, role="", first_min=FIRST_MIN, remind_h=REMIND_H):
    """st(dict, 제자리 수정 안 함) → (새 st, 보낼 문구 또는 None)."""
    st = dict(st or {})
    if ok:
        msg = None
        if st.get("down_alerted"):
            mins = (now - st.get("down_since", now)) / 60.0
            msg = "✅ %s 플러그 다시 응답(%s 끊겼었음)" % (name, _dur(mins))
        st.pop("down_since", None); st.pop("down_alerted", None)
        return st, msg
    if not st.get("down_since"):
        st["down_since"] = now
    mins = (now - st["down_since"]) / 60.0
    last = st.get("down_alerted")
    if mins >= first_min and (not last or now - last >= remind_h * 3600):
        st["down_alerted"] = now
        return st, "⚠ %s 플러그 %s째 응답 없음%s. 플러그가 뽑혔거나 Wi-Fi(Ryufarm)가 끊겼는지 확인" % (
            name, _dur(mins), (" — " + role) if role else "")
    return st, None


def _dur(mins):
    mins = int(round(mins))
    if mins >= 1440:
        return "%d일 %d시간" % (mins // 1440, mins % 1440 // 60)
    if mins >= 60:
        return "%d시간 %d분" % (mins // 60, mins % 60)
    return "%d분" % mins


def notify(msg):
    """텔레그램(대기열). 실패해도 제어에는 영향 없음."""
    if not msg:
        return
    try:
        import os, sys
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "notify"))
        import telegram_notify as tg
        tg.send(msg)
    except Exception:
        pass


def check(name, ok, role=""):
    """상태 파일(plug_alert_state.json, 기기 이름별)에 이어서 판단하고, 보낼 게 있으면 텔레그램. 제어 코드에서 매번 호출."""
    import json, os, time
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "plug_alert_state.json")
    try:
        d = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    st, msg = update(d.get(name), ok, time.time(), name, role)
    if st != d.get(name):
        d[name] = st
        try:
            with open(path + ".tmp", "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False)
            os.replace(path + ".tmp", path)
        except OSError:
            pass
    notify(msg)
    return msg
