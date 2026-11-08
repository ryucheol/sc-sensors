#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tuya 플러그 카운트다운(데드맨 fail-on) 시험 — 히터 제어 '죽으면 켜짐' 설계 확인용.

확인하는 것:
  1) 카운트다운 DP(기본 9)가 있는지
  2) 꺼진 상태에서 카운트다운이 끝나면 '켜짐'으로 바뀌는지
  3) 끝나기 전에 다시 걸면(재무장) 연장되는지
  4) 켜진 상태에서 0을 쓰면 카운트다운이 지워지는지

⚠ 시험 중 플러그가 실제로 켜지고 꺼진다. 히터·전열기가 아닌 전등이나 빈 플러그로 할 것.

실행(로컬키 필요, tinytuya 설치된 PC/Pi, 같은 와이파이):
  python3 plug_countdown_test.py --id <device id> --ip <ip> --key <local key> --ver 3.5
  python3 plug_countdown_test.py --keys tuya_keys.json --name "<기기 이름>"
옵션: --dp 9 (카운트다운 DP 번호), --sec 30 (시험 카운트다운 초)
"""
import argparse, json, sys, time

def pr(msg): print(time.strftime("[%H:%M:%S] ") + msg, flush=True)

def dps(d):
    s = d.status()
    if "dps" not in s: raise RuntimeError("상태조회 실패: %s" % s)
    return s["dps"]

def wait_relay(d, want, limit, step=3):
    t0 = time.time()
    while time.time() - t0 < limit:
        r = bool(dps(d).get("1"))
        if r == want: return time.time() - t0
        time.sleep(step)
    return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id"); ap.add_argument("--ip"); ap.add_argument("--key"); ap.add_argument("--ver", default="3.5")
    ap.add_argument("--keys"); ap.add_argument("--name")
    ap.add_argument("--dp", type=int, default=9); ap.add_argument("--sec", type=int, default=30)
    a = ap.parse_args()
    if a.keys:
        devs = json.load(open(a.keys))
        dev = next((x for x in devs if not a.name or x.get("name") == a.name), None)
        if not dev: sys.exit("기기를 찾지 못함: %s" % a.name)
        a.id, a.ip, a.key, a.ver = dev["id"], dev["ip"], dev["key"], str(dev["version"])
    if not (a.id and a.ip and a.key): sys.exit("--id --ip --key 또는 --keys 필요")

    import tinytuya
    d = tinytuya.OutletDevice(a.id, a.ip, a.key); d.set_version(float(a.ver)); d.set_socketTimeout(5)
    k = str(a.dp); S = a.sec; ok = {}

    s = dps(d); pr("DP 목록: %s" % s)
    ok["1 카운트다운 DP 존재"] = k in s
    if not ok["1 카운트다운 DP 존재"]:
        pr("DP%s 없음 — 다른 DP 번호일 수 있음(위 목록에서 0이거나 초 단위 숫자인 항목). --dp 로 다시 시험." % k)

    else:
        pr("── 시험2: 꺼짐 + %d초 카운트다운 → 켜지는지" % S)
        d.turn_off(); time.sleep(2); d.set_value(a.dp, S)
        t = wait_relay(d, True, S + 30)
        ok["2 끝나면 켜짐"] = t is not None
        pr("켜짐까지 %.0f초" % t if t is not None else "켜지지 않음(카운트다운이 '반대로 뒤집기'가 아닐 수 있음)")

        pr("── 시험3: 재무장 — %d초마다 다시 걸면 켜지지 않아야 함(%d초 관찰)" % (S // 2, S * 2))
        d.turn_off(); time.sleep(2)
        t0 = time.time(); flipped = False
        while time.time() - t0 < S * 2:
            d.set_value(a.dp, S); time.sleep(S // 2)
            if bool(dps(d).get("1")): flipped = True; break
        ok["3 재무장 연장"] = not flipped
        pr("재무장 중 켜짐 발생" if flipped else "재무장 동안 계속 꺼짐 유지")
        pr("   재무장 멈춤 → %d초 뒤 켜지는지" % S)
        t = wait_relay(d, True, S + 30)
        ok["3b 멈추면 켜짐"] = t is not None

        pr("── 시험4: 켜짐 상태에서 카운트다운 지우기(0) → 계속 켜져 있어야 함")
        d.turn_on(); time.sleep(2); d.set_value(a.dp, S); time.sleep(2); d.set_value(a.dp, 0)
        time.sleep(S + 10)
        ok["4 지우면 유지"] = bool(dps(d).get("1"))

    d.set_value(a.dp, 0) if k in dps(d) else None
    print("\n결과"); [print("  %s %s" % ("✅" if v else "❌", n)) for n, v in ok.items()]
    print("시험 끝: 플러그는 켜진 상태로 둠(필요하면 앱에서 끄기).")

if __name__ == "__main__":
    main()
