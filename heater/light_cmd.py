#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""전등 켜기/끄기(Tuya Wi-Fi 스위치 모듈, 로컬) — 텔레그램·Pi 터미널 '다락등 켜|꺼'.

다락등: Tuya 2-Gang On/Off Module WF+RF(Smart Life '다락등', 10.42.0.85, v3.5). 스위치 2 = 다락등2(연결됨),
        스위치 1 = 비어 있음(등 미연결, 11-20). Google Home(Smart Life 연동) 음성과 433 MHz 리모컨으로도 켜고 끈다
        — 상태는 기기에서 그대로 읽으므로 음성·리모컨으로 바꿔도 Pi가 안다.
기기 키: tuya_keys.json 의 name "Attic light"(저장소 밖 비밀).
실행: ~/tuya-venv/bin/python heater/light_cmd.py [다락등2] [켜|꺼|상태]
"""
import json, os, sys

HERE = os.path.dirname(os.path.realpath(__file__))
BASE = os.path.dirname(HERE)
KEYS = os.path.join(BASE, "tuya_keys.json")

# 이름 → (tuya_keys 이름, DP 번호). 별칭은 같은 등으로.
LIGHTS = {"다락등2": ("Attic light", 2)}
ALIASES = {"다락등": "다락등2", "다락": "다락등2", "다락불": "다락등2", "attic": "다락등2", "light": "다락등2", "전등": "다락등2"}
ON = {"켜", "켜기", "켬", "on"}
OFF = {"꺼", "끄기", "끔", "off"}


def parse(toks):
    """(이름, 'on'|'off'|None) — 순수. 이름이 없으면 기본(다락등2)."""
    name, act = None, None
    for t in toks:
        w = t.lower().rstrip("을를,.")
        if w in LIGHTS: name = w
        elif w in ALIASES: name = ALIASES[w]
        elif w in ON: act = "on"
        elif w in OFF: act = "off"
        elif w in ("상태", ""): pass
        else: raise ValueError("명령: 다락등 / 다락등 켜 / 다락등 꺼")
    return name or "다락등2", act


def device(dev_name):
    import tinytuya
    d = next(x for x in json.load(open(KEYS)) if x.get("name") == dev_name)
    dev = tinytuya.Device(d["id"], d["ip"], d["key"], version=float(d.get("version", 3.5)))
    dev.set_socketTimeout(4); dev.set_socketRetryLimit(2)
    return dev


def main(argv):
    try:
        name, act = parse(argv)
    except ValueError as e:
        print(e); return 2
    dev_name, dp = LIGHTS[name]
    try:
        dev = device(dev_name)
        if act:
            dev.set_status(act == "on", dp)
        st = dev.status() or {}
        on = (st.get("dps") or {}).get(str(dp))
    except Exception as e:
        print("%s 연결 실패: %s" % (name, e)); return 1
    if on is None:
        print("%s 상태 읽기 실패: %s" % (name, st.get("Err") or st)); return 1
    print("%s %s%s" % (name, "켜짐" if on else "꺼짐", "" if act else " (명령: 다락등 켜|꺼)"))
    return 0 if (act is None or on == (act == "on")) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
