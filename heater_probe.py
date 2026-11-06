#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""heater_probe.py — 플러그 실측 전력을 몇 분간 샘플링해 '히터 자체 서모스탯/과열방지'의 개폐(가동 비율)를 확인한다.

  ~/tuya-venv/bin/python heater_probe.py              # 180초, 5초 간격
  ~/tuya-venv/bin/python heater_probe.py --secs 300 --every 3

- 읽기 전용: 플러그를 제어하지 않는다. 키(tuya_keys.json)는 읽기만 하고 출력하지 않는다.
- 제어기(heater-control)와 동시에 플러그에 접속하므로 heater.log에 '상태조회 오류'가 1~2회 찍힐 수 있다(무해).
- 릴레이가 ON인데 전력이 ON_W(300W) 미만이면 '꺼짐(히터가 안 끎)'으로 센다.
"""
import os, sys, json, time, argparse

BASE = os.path.expanduser("~/smart-cabin")
KEYS = os.path.join(BASE, "tuya_keys.json")
ON_W = 300.0          # 이 이상이면 '가열 중'으로 본다


def summarize(samples, every, on_w=ON_W):
    """samples: [(t_sec, relay(bool|None), watt(float|None))], every: 샘플 간격(초) → 요약 dict"""
    ok = [s for s in samples if s[2] is not None]
    if not ok:
        return {"n": 0}
    n = len(ok)
    relay_on = [bool(s[1]) for s in ok]
    heating = [bool(s[1]) and s[2] >= on_w for s in ok]
    n_relay_on = sum(relay_on)
    duty = (sum(heating) / n_relay_on) if n_relay_on else None      # 릴레이 ON 구간에서 실제 가열 비율
    # 릴레이 ON 구간 안에서 가열↔무가열 전환 횟수와 가장 긴 무가열 연속시간
    seq = [h for h, r in zip(heating, relay_on) if r]
    trans = sum(1 for a, b in zip(seq, seq[1:]) if a != b)
    longest, run = 0, 0
    for h in seq:
        run = 0 if h else run + 1
        longest = max(longest, run)
    watts_on = [s[2] for s, h in zip(ok, heating) if h]
    return {"n": n, "relay_on_n": n_relay_on, "duty": duty, "transitions": trans,
            "longest_off_s": longest * every,
            "mean_w_all": sum(s[2] for s in ok) / n,
            "mean_w_when_heating": (sum(watts_on) / len(watts_on)) if watts_on else 0.0,
            "min_w": min(s[2] for s in ok), "max_w": max(s[2] for s in ok)}


def verdict(r):
    if not r.get("n"):
        return "샘플 없음(플러그 응답 없음)."
    if not r["relay_on_n"]:
        return "측정 중 릴레이가 OFF였다(제어기가 껐음) — 측정 의미 없음."
    d = r["duty"]
    if d >= 0.97:
        return "히터가 거의 계속 가열 중(서모스탯 개입 없음)."
    if d >= 0.05:
        return "히터가 켜졌다 꺼졌다 한다 → 히터 자체 서모스탯/과열방지가 개입 중. 다이얼 최대·통풍구 막힘·타이머 확인."
    return "릴레이는 ON인데 히터가 거의 전력을 안 쓴다 → 히터 스위치/다이얼/타이머/과열방지 확인."


def main():
    ap = argparse.ArgumentParser(description="플러그 전력 샘플링(읽기 전용)")
    ap.add_argument("--secs", type=float, default=180.0, help="측정 시간(초)")
    ap.add_argument("--every", type=float, default=5.0, help="샘플 간격(초)")
    a = ap.parse_args()

    import tinytuya      # 지연 임포트(요약 함수 테스트에 tinytuya 불필요)
    devs = json.load(open(KEYS))
    dev = next((d for d in devs if d.get("name") == "Main heater"), devs[0])
    p = tinytuya.OutletDevice(dev["id"], dev["ip"], dev["key"])
    p.set_version(float(dev["version"])); p.set_socketTimeout(4)

    print("측정 시작: %.0f초, %.0f초 간격 (Ctrl+C로 중단하면 지금까지 요약)" % (a.secs, a.every))
    samples, errs, t0 = [], 0, time.time()
    try:
        while time.time() - t0 < a.secs:
            t = time.time() - t0
            try:
                s = p.status().get("dps", {})
                relay, watt = bool(s.get("1")), s.get("19", 0) / 10.0
                samples.append((t, relay, watt))
                print("%s  릴레이 %-3s  %6.1f W  %s" % (time.strftime("%H:%M:%S"), "ON" if relay else "OFF", watt,
                                                        "가열" if (relay and watt >= ON_W) else ("—" if not relay else "무가열")))
            except Exception as e:
                errs += 1; print("%s  (읽기 실패: %s)" % (time.strftime("%H:%M:%S"), e))
            time.sleep(a.every)
    except KeyboardInterrupt:
        print("\n(중단)")

    r = summarize(samples, a.every)
    print("\n=== 요약 ===")
    if r.get("n"):
        print("샘플 %d개(읽기 실패 %d), 릴레이 ON 샘플 %d개" % (r["n"], errs, r["relay_on_n"]))
        if r["duty"] is not None:
            print("가열 비율: %.0f%%  | 가열↔무가열 전환 %d회 | 가장 긴 무가열 %.0f초" % (r["duty"] * 100, r["transitions"], r["longest_off_s"]))
        print("전력 평균 %.0f W (가열 중 평균 %.0f W), 최소 %.0f W, 최대 %.0f W" % (r["mean_w_all"], r["mean_w_when_heating"], r["min_w"], r["max_w"]))
    print("판정:", verdict(r))


if __name__ == "__main__":
    main()
