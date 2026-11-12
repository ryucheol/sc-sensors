#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shelly 동파 가드 설치·갱신 도구 (Pi에서 실행).

1. sensor_keys.json(gitignore)의 '내부1층'·'바닥외부' bindkey를 플러그 KVS(mbkey_floor1/mbkey_out)에 넣는다.
   키 값은 화면에 출력하지 않는다. 키는 LAN 안에서 Pi → 플러그로만 간다.
2. heater/shelly_frost_guard.js 를 플러그 스크립트(이름 frost_guard)로 올리고 부팅 자동 시작·실행.
3. 상태를 보여 준다(센서별 수신 여부·나이, 키 적재 여부, 모드).

사용:
  python3 heater/shelly_guard_setup.py <플러그IP>            # 시험 모드(ACT:false) 그대로
  python3 heater/shelly_guard_setup.py <플러그IP> --act      # 실제 동작(ACT:true)
  python3 heater/shelly_guard_setup.py <플러그IP> --status   # 상태만
"""
import json, os, sys, time, urllib.request

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
LABELS = {"내부1층": "mbkey_floor1", "바닥외부": "mbkey_out"}


def rpc(ip, method, params=None):
    req = urllib.request.Request("http://%s/rpc" % ip, data=json.dumps({"id": 1, "method": method, "params": params or {}}).encode(),
                                 headers={"Content-Type": "application/json"})
    r = json.loads(urllib.request.urlopen(req, timeout=15).read())
    if "error" in r: raise RuntimeError("%s: %s" % (method, r["error"]))
    return r["result"]


def status(ip):
    s = json.loads(urllib.request.urlopen("http://%s/script/%d/status" % (ip, find_id(ip)), timeout=10).read())
    print("모드 %s  ACT %s  실내 %s  예측 %s  하트비트 %s초 전  마지막 %s" % (
        s["mode"], s["act"], s["t_in"], s["t_pred"], s["hb_age"], s["last_act"] or "-"))
    for k in ("floor1", "qp", "out"):
        e = s[k]; print("  %-6s t=%s age=%s n=%s rssi=%s bad=%s" % (k, e["t"], e["age"], e["n"], e["rssi"], e.get("bad")))
    print("  키 적재:", s["keys"])


def find_id(ip):
    for sc in rpc(ip, "Script.List")["scripts"]:
        if sc["name"] == "frost_guard": return sc["id"]
    return None


def main():
    ip = sys.argv[1]; act = "--act" in sys.argv
    if "--status" in sys.argv: return status(ip)
    kf = os.path.join(ROOT, "sensor_keys.json")
    keys = json.load(open(kf, encoding="utf-8")) if os.path.exists(kf) else {}
    if not keys: print("sensor_keys.json 없음 — pvvx 키 없이 올림(Qingping만 사용)")
    for label, kvs in LABELS.items():
        if not keys: break
        k = (keys.get(label) or {}).get("key", "")
        if len(k) != 32: print("⚠ %s 키 없음/형식 오류 — 건너뜀" % label); continue
        rpc(ip, "KVS.Set", {"key": kvs, "value": k}); print("키 넣음:", label, "→", kvs)
    rpc(ip, "BLE.SetConfig", {"config": {"enable": True}})
    code = open(os.path.join(HERE, "shelly_frost_guard.js"), encoding="utf-8").read()
    code = "\n".join(l for l in code.splitlines() if not l.strip().startswith("//"))
    if act: code = code.replace("ACT: false", "ACT: true")
    assert all(ord(c) < 128 for c in code), "스크립트에 비ASCII 문자 — PutCode가 거절함"
    sid = find_id(ip)
    if sid is None: sid = rpc(ip, "Script.Create", {"name": "frost_guard"})["id"]
    else: rpc(ip, "Script.Stop", {"id": sid})
    for k in range(0, len(code), 900):
        rpc(ip, "Script.PutCode", {"id": sid, "code": code[k:k + 900], "append": k > 0})
    rpc(ip, "Script.SetConfig", {"id": sid, "config": {"enable": True}})
    rpc(ip, "Script.Start", {"id": sid})
    print("가드 올림: script %d, %d바이트, ACT=%s" % (sid, len(code), act))
    time.sleep(3); status(ip)


if __name__ == "__main__":
    main()
