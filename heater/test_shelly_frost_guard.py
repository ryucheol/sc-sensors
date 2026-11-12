#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""shelly_frost_guard.js(v2)의 판단 로직을 실제 플러그에서 시나리오로 검증한다.

운영 중인 가드는 건드리지 않고, 같은 코드를 '임시 시험 스크립트'로 올린 뒤
시험 키로 암호화한 pvvx 프레임과 Qingping 값을 직접 주입(handleAdv)하고 tick()을 불러 결정을 본다.
실제 Qingping 광고가 섞이지 않게 시험 스크립트에서는 QP_MAC을 바꾼다. ACT=false라 스위치는 안 바뀐다.

사용: python3 heater/test_shelly_frost_guard.py 192.168.0.188
"""
import json, math, os, struct, sys, urllib.request
from cryptography.hazmat.primitives.ciphers.aead import AESCCM

HERE = os.path.dirname(os.path.realpath(__file__))
F1, OUT = "a4:c1:38:6a:77:be", "a4:c1:38:b4:5e:13"


def rpc(ip, method, params):
    req = urllib.request.Request("http://%s/rpc" % ip, data=json.dumps({"id": 1, "method": method, "params": params}).encode(),
                                 headers={"Content-Type": "application/json"})
    r = json.loads(urllib.request.urlopen(req, timeout=15).read())
    return r.get("result", r.get("error"))


def frame(key, mac, cnt, temp, ext=b"\x01\x02\x03"):
    mac_le = bytes(reversed(bytes.fromhex(mac.replace(":", ""))))
    head = struct.pack("<HHB", 0x5858, 0x055B, cnt & 0xFF) + mac_le
    pt = struct.pack("<HBh", 0x1004, 2, int(round(temp * 10)))
    ct = AESCCM(key, tag_length=4).encrypt(mac_le + head[2:5] + ext, pt, b"\x11")
    return head + ct[:-4] + ext + ct[-4:]


def main(ip):
    code = open(os.path.join(HERE, "shelly_frost_guard.js"), encoding="utf-8").read()
    code = "\n".join(l for l in code.splitlines() if not l.strip().startswith("//"))
    code = code.replace('QP_MAC: "58:2d:34:87:2f:64"', 'QP_MAC: "00:00:00:00:00:00"')   # 실제 Qingping 차단
    k1, k2 = os.urandom(16), os.urandom(16)
    # 시험 중 실제 기상청 호출 막기(FC.try_ts를 미래로) — 예보는 시나리오에서 직접 넣는다
    code += ('\nKEY.floor1=mb_hexkey("%s"); KEY.out=mb_hexkey("%s"); FC.try_ts=now()+999999;\n' % (k1.hex(), k2.hex()) +
             'function hs(h){let s="";for(let i=0;i<h.length;i+=2)s+=String.fromCharCode(parseInt(h.substr(i,2),16));return s;}\n'
             'function inj(mac,h){handleAdv(mac,{"fe95":hs(h)},-50);}\n'
             'function run(){st.want=null;tick();return JSON.stringify({mode:st.mode,want:st.want,t_in:st.t_in,t_pred:st.t_pred,'
             'f1:S.floor1.t,out:S.out.t,bad:S.floor1.bad+S.out.bad});}\n')
    sid = rpc(ip, "Script.Create", {"name": "guard_test"})["id"]
    cnt = [0]

    def ev(c):
        return rpc(ip, "Script.Eval", {"id": sid, "code": c})

    def put(mac, key, t):
        cnt[0] += 1
        return ev('inj("%s","%s")' % (mac, frame(key, mac, cnt[0], t).hex()))

    try:
        for k in range(0, len(code), 900):
            rpc(ip, "Script.PutCode", {"id": sid, "code": code[k:k + 900], "append": k > 0})
        rpc(ip, "Script.Start", {"id": sid})
        decay = math.exp(-60 / 550)
        cases = []   # (설명, 준비 코드 또는 주입, 기대 mode, 기대 want)

        def case(name, f1=None, out=None, qp=None, stale=(), want=None, mode=None, fc=None):
            if f1 is not None: put(F1, k1, f1)
            if out is not None: put(OUT, k2, out)
            if qp is not None: ev("S.qp.t=%s;S.qp.ts=now();" % qp)
            for s in stale: ev("S.%s.ts=0;" % s)
            ev("FC.ts=0;" if fc is None else "FC.min6=%s;FC.ts=now();" % fc)
            r = json.loads(ev("run()")["result"])
            ok = r["want"] == want and (mode is None or r["mode"] == mode)
            print(("OK  " if ok else "FAIL"), name.ljust(42), r)
            return ok

        res = []
        res.append(case("1층 10 / 외기 2 -> 끔(예측 %.1f)" % (2 + 8 * decay), f1=10.0, out=2.0, want=False, mode="frost+out"))
        res.append(case("1층 6 / 외기 -10 -> 끔(예측 %.1f)" % (-10 + 16 * decay), f1=6.0, out=-10.0, want=False))
        res.append(case("1층 4 / 외기 -10 -> 미리 켬(예측 %.1f)" % (-10 + 14 * decay), f1=4.0, out=-10.0, want=True))
        res.append(case("1층 3.5 / 외기 -10 -> 켬(예측 %.1f)" % (-10 + 13.5 * decay), f1=3.5, out=-10.0, want=True))
        res.append(case("1층 4 / 외기 없음 -> 유지(띠 안)", f1=4.0, stale=("out",), want=None, mode="frost"))
        res.append(case("1층 6 + Qingping 2.5 -> 낮은 쪽, 켬", f1=6.0, qp=2.5, want=True))
        res.append(case("실내 없음 / 외기 2 -> 켬", out=2.0, stale=("floor1", "qp"), want=True, mode="out-only"))
        res.append(case("실내 없음 / 외기 8 -> 끔", out=8.0, stale=("floor1", "qp"), want=False, mode="out-only"))
        res.append(case("센서 전부 없음 -> 켬", stale=("floor1", "qp", "out"), want=True, mode="blind"))
        res.append(case("1층 4 / 외기 2 / 예보 -10 -> 예보로 켬", f1=4.0, out=2.0, fc=-10, want=True, mode="frost+out"))
        res.append(case("1층 3.2 / 외기센서 없음 / 예보 -5 -> 켬", f1=3.2, stale=("out", "qp"), fc=-5, want=True, mode="frost+out"))
        res.append(case("1층 8 / 외기 6 / 예보 4 -> 끔", f1=8.0, out=6.0, fc=4, want=False))
        ev("S.floor1.ts=now();S.floor1.t=10;FC.ts=0;")
        bad = bytearray(frame(k1, F1, 200, -20.0)); bad[-1] ^= 1
        ev('inj("%s","%s")' % (F1, bad.hex()))
        r = json.loads(ev("run()")["result"])
        okb = r["f1"] == 10 and r["bad"] >= 1
        print(("OK  " if okb else "FAIL"), "변조 프레임 무시(-20 주입)".ljust(42), r)
        res.append(okb)
        sys.exit(0 if all(res) else 1)
    finally:
        rpc(ip, "Script.Stop", {"id": sid}); rpc(ip, "Script.Delete", {"id": sid})


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "192.168.0.188")
