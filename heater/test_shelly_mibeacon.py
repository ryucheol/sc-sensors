#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""shelly_mibeacon.js(플러그 내장 MiBeacon AES-CCM 해독)를 실제 플러그에서 검증한다.

PC/Pi에서 cryptography의 AESCCM으로 시험용 키(무작위, 진짜 bindkey 아님)로 프레임을 만들고,
플러그에 '임시 시험 스크립트'를 올려 Script.Eval로 해독시킨 뒤 결과를 비교하고 스크립트를 지운다.
⚠ 운영 중인 스크립트(동파 가드)에서 Script.Eval로 실험하지 말 것 — 잡히지 않은 예외 한 번에 스크립트가 멈춘다(11-13 실제로 겪음).

사용: python3 heater/test_shelly_mibeacon.py 192.168.0.188
2026-11-13 Plug M Gen3 fw 1.8.99: 온도(-12.3)·습도·배터리·온습도(0x100D) 4건 일치, 태그 변조·틀린 키는 null.
"""
import json, os, struct, sys, urllib.request
from cryptography.hazmat.primitives.ciphers.aead import AESCCM

HERE = os.path.dirname(os.path.realpath(__file__))


def rpc(ip, method, params):
    req = urllib.request.Request("http://%s/rpc" % ip, data=json.dumps({"id": 1, "method": method, "params": params}).encode(),
                                 headers={"Content-Type": "application/json"})
    r = json.loads(urllib.request.urlopen(req, timeout=15).read())
    return r.get("result", r.get("error"))


def frame(key, mac, cnt, objs, ext=b"\x01\x02\x03"):
    """pvvx 'Mi encrypted'와 같은 배치의 MiBeacon(서비스데이터 0xFE95) 바이트."""
    mac_le = bytes(reversed(bytes.fromhex(mac.replace(":", ""))))
    head = struct.pack("<HHB", 0x5858, 0x055B, cnt) + mac_le
    pt = b"".join(struct.pack("<HB", oid, len(v)) + v for oid, v in objs)
    ct = AESCCM(key, tag_length=4).encrypt(mac_le + head[2:5] + ext, pt, b"\x11")
    return head + ct[:-4] + ext + ct[-4:]


def main(ip):
    lib = open(os.path.join(HERE, "shelly_mibeacon.js"), encoding="utf-8").read()
    lib = "\n".join(l for l in lib.splitlines() if not l.strip().startswith("//"))
    lib += ('\nfunction t(h,kh){let s="";for(let i=0;i<h.length;i+=2)s+=String.fromCharCode(parseInt(h.substr(i,2),16));'
            'return JSON.stringify(mb_decrypt(s,mb_hexkey(kh)));}\n')
    sid = rpc(ip, "Script.Create", {"name": "mb_test"})["id"]
    try:
        for k in range(0, len(lib), 900):
            rpc(ip, "Script.PutCode", {"id": sid, "code": lib[k:k + 900], "append": k > 0})
        rpc(ip, "Script.Start", {"id": sid})
        key = os.urandom(16); kh = key.hex(); mac = "A4:C1:38:00:00:01"
        cases = [({"temp": -12.3}, [(0x1004, struct.pack("<h", -123))]),
                 ({"humi": 55.5}, [(0x1006, struct.pack("<H", 555))]),
                 ({"batt": 73}, [(0x100A, bytes([73]))]),
                 ({"temp": 21.7, "humi": 48}, [(0x100D, struct.pack("<hH", 217, 480))])]
        fails = 0
        for i, (want, objs) in enumerate(cases):
            got = json.loads(rpc(ip, "Script.Eval", {"id": sid, "code": 't("%s","%s")' % (frame(key, mac, i, objs).hex(), kh)})["result"])
            print(("OK  " if got == want else "FAIL"), want, got); fails += got != want
        bad = bytearray(frame(key, mac, 9, [(0x1004, b"\x32\x00")])); bad[-1] ^= 1
        r = rpc(ip, "Script.Eval", {"id": sid, "code": 't("%s","%s")' % (bad.hex(), kh)})["result"]
        print(("OK  " if r == "null" else "FAIL"), "tampered tag ->", r); fails += r != "null"
        sys.exit(1 if fails else 0)
    finally:
        rpc(ip, "Script.Stop", {"id": sid}); rpc(ip, "Script.Delete", {"id": sid})


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "192.168.0.188")
