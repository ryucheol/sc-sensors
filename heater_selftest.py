#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""떠나기 전 히터 자가점검 — 플러그를 잠깐 켜 실제 전력을 읽어 '히터가 진짜 데워지는지' 확인.
   venv(~/tuya-venv)로 실행(tinytuya 필요). 별칭: 히터점검
   PASS: 가열 전력 측정됨 / FAIL: 켰는데 전력 0 → 히터 자체 스위치·다이얼·콘센트 확인."""
import os, json, time, tinytuya

BASE = os.path.expanduser("~/smart-cabin")

def main():
    k = json.load(open(os.path.join(BASE, "tuya_keys.json")))[0]
    d = tinytuya.OutletDevice(k["id"], k["ip"], k["key"])
    d.set_version(float(k["version"])); d.set_socketTimeout(5)

    try:
        pre = bool(d.status().get("dps", {}).get("1"))
    except Exception as e:
        print("플러그 응답 없음 — 네트워크/플러그 확인:", e); return

    print("히터 자가점검: 플러그 5초 켜서 전력 측정...")
    d.turn_on()
    mx = 0.0
    for _ in range(5):
        time.sleep(1)
        try:
            w = d.status().get("dps", {}).get("19", 0) / 10.0
        except Exception:
            w = 0.0
        mx = max(mx, w)
        print("  %.0fW" % w)
    if not pre:          # 점검 전 꺼져 있었으면 원복
        d.turn_off()

    print("최대 %.0fW → " % mx, end="")
    if   mx >= 500: print("PASS ✅  히터 정상 가열")
    elif mx < 100:  print("FAIL ❌  히터가 전력을 안 먹음 — 히터 자체 스위치/다이얼/콘센트 확인")
    else:           print("WARN ⚠  전력이 약함 — 히터 모드(약/강)·다이얼 확인")

if __name__ == "__main__":
    main()
