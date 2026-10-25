#!/usr/bin/env python3
"""
passive_current.py — 순정 샤오미 센서의 '현재값'을 '연결 없이' 비콘(MiBeacon/FE95)에서 받는다.

- 연결 안 함 → 광고만 수동 수신(passive) → 센서 배터리 영향 거의 없음, 연결 실패 없음.
- 암호화된 MiBeacon을 기기별 bindkey(sensor_keys.json)로 AES-CCM 복호화.
- 순정은 온/습/배터리를 '따로, 드물게' 방송 → 온+습 다 모으려면 잠깐 들어야 함.

사용:
    python passive_current.py --once                  # 세 센서 온+습 다 모이면 종료
    python passive_current.py --once --upload          # 모은 뒤 '농막_현재' 탭에 업로드
    python passive_current.py --secs 120               # 고정 120초 수신

bindkey는 비밀 → sensor_keys.json 은 .gitignore(커밋 금지). 업로드엔 환경변수 FARMHUT_SECRET 필요.
"""
import argparse
import asyncio
import json
import os
import struct
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

from bleak import BleakScanner
from cryptography.hazmat.primitives.ciphers.aead import AESCCM

FE95 = "0000fe95-0000-1000-8000-00805f9b34fb"
CFG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sensor_keys.json")

WEBAPP_URL = "https://script.google.com/macros/s/AKfycbzEJn5u4lIAJprA69xR_yRSEeRmk2I_qe_igsAlPqEq1umHdsNkykOcF_imW3Zj5s_Rzg/exec"
SECRET = os.environ.get("FARMHUT_SECRET", "CHANGE-ME")


def load_cfg():
    with open(CFG, encoding="utf-8") as f:
        return json.load(f)


def decrypt_mibeacon(sd, key):
    if len(sd) < 15:
        return None
    frctrl = int.from_bytes(sd[0:2], "little")
    if not (frctrl & 0x08):
        return None
    i = 5
    if frctrl & 0x10:
        i += 6
    if frctrl & 0x20:
        cap = sd[i]; i += 1
        if cap & 0x20:
            i += 2
    nonce = bytes(sd[5:11]) + bytes(sd[2:5]) + bytes(sd[-7:-4])
    mic = bytes(sd[-4:])
    ct = bytes(sd[i:-7])
    if not ct:
        return None
    try:
        return AESCCM(key, tag_length=4).decrypt(nonce, ct + mic, b"\x11")
    except Exception:
        return None


def parse_objects(pt):
    out = {}
    idx = 0
    while idx + 3 <= len(pt):
        oid = int.from_bytes(pt[idx:idx + 2], "little")
        ln = pt[idx + 2]
        val = pt[idx + 3:idx + 3 + ln]
        if len(val) < ln:
            break
        if oid == 0x1004 and ln == 2:
            out["temp"] = struct.unpack("<h", val)[0] / 10
        elif oid == 0x1006 and ln == 2:
            out["humi"] = struct.unpack("<H", val)[0] / 10
        elif oid == 0x100D and ln == 4:
            t, h = struct.unpack("<hH", val)
            out["temp"] = t / 10; out["humi"] = h / 10
        elif oid == 0x100A and ln == 1:
            out["batt"] = val[0]
        idx += 3 + ln
    return out


def post_current(readings):
    body = json.dumps({"secret": SECRET, "mode": "current", "readings": readings}).encode("utf-8")
    req = urllib.request.Request(WEBAPP_URL, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


async def main():
    ap = argparse.ArgumentParser(description="순정 샤오미 센서 현재값 passive 수신(무연결)")
    ap.add_argument("--secs", type=float, default=300.0, help="최대 수신 시간(초)")
    ap.add_argument("--once", action="store_true", help="세 센서 온+습 다 모이면 조기 종료")
    ap.add_argument("--upload", action="store_true", help="모은 뒤 '농막_현재' 탭에 업로드")
    args = ap.parse_args()

    if args.upload and SECRET in ("", "CHANGE-ME"):
        print("[중단] 업로드하려면 환경변수 FARMHUT_SECRET 를 설정하세요.")
        sys.exit(2)

    cfg = load_cfg()
    by_mac = {v["mac"].upper(): (label, bytes.fromhex(v["key"])) for label, v in cfg.items()}
    latest = {label: {} for label in cfg}
    tdone = {}          # label -> 온+습 완성된 시각(경과초)
    done = asyncio.Event()
    t0 = time.perf_counter()

    def complete(label):
        return "temp" in latest[label] and "humi" in latest[label]

    def cb(dev, adv):
        mac = (dev.address or "").upper()
        if mac not in by_mac:
            return
        sd = adv.service_data.get(FE95)
        if not sd:
            return
        label, key = by_mac[mac]
        pt = decrypt_mibeacon(sd, key)
        if pt is None:
            return
        vals = parse_objects(pt)
        if not vals:
            return
        was = complete(label)
        latest[label].update(vals)
        el = time.perf_counter() - t0
        print(f"[+{el:5.1f}s] {label}: {vals}")
        if not was and complete(label):
            tdone[label] = el
            print(f"         └ {label} 온+습 완성 ({el:.1f}s)")
        if args.once and all(complete(l) for l in cfg):
            done.set()

    print(f"[passive] 무연결 수신 시작 (최대 {args.secs:.0f}초, --once={args.once})…\n")
    scanner = BleakScanner(detection_callback=cb)
    await scanner.start()
    try:
        await asyncio.wait_for(done.wait(), args.secs)
    except asyncio.TimeoutError:
        pass
    finally:
        await scanner.stop()

    total = time.perf_counter() - t0
    print("\n=== 최종 현재값 ===")
    now_ms = time.time() * 1000
    readings = []
    for label in cfg:
        v = latest[label]
        parts = []
        if "temp" in v: parts.append(f"{v['temp']:.1f}℃")
        if "humi" in v: parts.append(f"{v['humi']:.1f}%")
        if "batt" in v: parts.append(f"배터리 {v['batt']}%")
        done_s = f"(완성 {tdone[label]:.1f}s)" if label in tdone else "(미완성)"
        print(f"  {label}: " + (" / ".join(parts) if parts else "(수신 없음)") + f"  {done_s}")
        if "temp" in v or "humi" in v:
            r = {"sensor": label, "time_ms": now_ms}
            if "temp" in v: r["temp"] = round(v["temp"], 1)
            if "humi" in v: r["hum"] = v["humi"]
            if "batt" in v: r["volt"] = round(2.2 + 0.9 * v["batt"] / 100, 3)
            readings.append(r)

    print(f"\n[시간] 전체 {total:.1f}s" +
          (f" · 완성 센서 {len(tdone)}/{len(cfg)}" if args.once else ""))

    if args.upload and readings:
        try:
            res = post_current(readings)
            print(f"[업로드] {res.get('updated')}개 갱신" if res.get("ok") else f"[업로드 거부] {res.get('error')}")
        except Exception as e:
            print(f"[업로드 오류] {e}")


if __name__ == "__main__":
    asyncio.run(main())
