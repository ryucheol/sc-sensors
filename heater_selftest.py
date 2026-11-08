#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""떠나기 전 히터 자가점검 — 플러그를 켜 실제 전력을 읽어 '히터가 진짜 데워지는지' 확인.
   venv(~/tuya-venv)로 실행(tinytuya 필요). 별칭: 히터점검
   사용: 히터점검 [최대초]   (기본 25초, 예: 히터점검 30)
   ※ 이 플러그는 켠 뒤 ~12초쯤 지나야 전력을 보고함 → 전력 잡히면 즉시 PASS,
     안 잡히면 최대초까지 기다렸다 판정. 전압은 있는데 전류만 0이면 플러그 정상·히터쪽 문제."""
import os, sys, json, time, tinytuya
sys.path.insert(0, os.path.expanduser("~/smart-cabin"))
import heater_control as hc   # 순수 함수만 사용(detect_stage·stage_advice·forecast_min_temp)

BASE = os.path.expanduser("~/smart-cabin")
PASS_W = 500.0      # 이 이상이면 가열 확인
MAXW_NO = 100.0     # 이 미만이면 무부하

def main():
    maxs = 25
    if len(sys.argv) > 1:
        try: maxs = max(5, int(float(sys.argv[1])))
        except ValueError: pass

    k = json.load(open(os.path.join(BASE, "tuya_keys.json")))[0]
    d = tinytuya.OutletDevice(k["id"], k["ip"], k["key"])
    d.set_version(float(k["version"])); d.set_socketTimeout(5)

    try:
        pre = bool(d.status().get("dps", {}).get("1"))
    except Exception as e:
        print("플러그 응답 없음 — 네트워크/플러그 확인:", e); return

    print("히터 자가점검: 플러그 켜고 전력 측정(최대 %d초, 잡히면 조기 종료)..." % maxs)
    d.turn_on()
    mx = 0.0; V = 0.0; hit = None; t0 = time.time()
    while time.time() - t0 < maxs:
        try: d.updatedps([18, 19, 20])     # 전력계 갱신 강제 요청
        except Exception: pass
        time.sleep(1)
        try:
            s = d.status().get("dps", {})
            w = s.get("19", 0) / 10.0
            V = s.get("20", 0) / 10.0
        except Exception:
            w = 0.0
        mx = max(mx, w)
        el = int(time.time() - t0)
        print("  +%2ds: %.0fW (V=%.0f)" % (el, w, V))
        if w >= PASS_W:
            hit = el; break
    if not pre:
        d.turn_off()

    # 단수·예보·조언(떠나기 전 확인용): 지금 실측 W로 단을 판정하고, 동파모드 기준으로 48h 예보 최저와 금요일 예열 가능 여부를 본다
    try:
        cfg = json.load(open(os.path.join(BASE, "heater_config.json")))
        sg = hc.detect_stage(mx, cfg.get("stages", hc.STAGES_DEFAULT))
        tmin = hc.forecast_min_temp(hours=48)
        ua = cfg["preheat"]["ua_w_per_k"]
        if sg:
            teq = (tmin + mx / ua) if tmin is not None else None
            print("히터 단수: %d단 (%.0fW) · 48h 예보 최저 %s°C → 도달가능 최고 %s°C" % (
                sg, mx, "?" if tmin is None else "%.0f" % tmin, "?" if teq is None else "%.0f" % teq))
            adv = hc.stage_advice(cfg, "frost", sg, mx, tmin, None, None, None, False, 0, False)
            print("조언: " + adv if adv else "조언: 현재 단수로 동파 유지·금요일 예열 모두 가능")
        else:
            print("히터 단수: 미상(전력 %.0fW)" % mx)
    except Exception as e:
        print("단수 판정 생략: %s" % e)
    if mx >= PASS_W:
        print("최대 %.0fW (%ds째 감지) → PASS ✅  히터 정상 가열" % (mx, hit))
    elif mx < MAXW_NO:
        if V >= 150:
            print("최대 %.0fW, 전압 %.0fV → FAIL ❌  플러그는 정상(전압 있음)인데 히터 무부하 — 히터 버튼(40/60/100%)·내장 서모스탯·콘센트 확인" % (mx, V))
        else:
            print("최대 %.0fW, 전압 %.0fV → FAIL ❌  전압도 없음 — 플러그/배선/차단기 확인" % (mx, V))
    else:
        print("최대 %.0fW → WARN ⚠  전력이 약함 — 히터 버튼(40/60/100%) 확인" % mx)

if __name__ == "__main__":
    main()
