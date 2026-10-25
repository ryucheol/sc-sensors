#!/usr/bin/env python3
"""
current_sync.py — 세 센서의 '현재값'만 빠르게 읽어 '농막_현재' 탭에 업데이트(가벼운 실행).

이력(history) 다운로드 없이, 한 번의 스캔으로 3개를 찾아 각각 현재값(ebe0ccc1) 1회만 읽는다.
→ farmhut_sync.py(이력 동기화)보다 훨씬 가볍고 빠름.

사용:
    python current_sync.py            # 읽어서 '농막_현재' 탭 업데이트
    python current_sync.py --dry-run  # 업로드 없이 읽기 + 소요시간만 측정

비밀키: 환경변수 FARMHUT_SECRET (run_sync 와 동일)
"""

import argparse
import asyncio
import datetime
import json
import os
import struct
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

from bleak import BleakClient, BleakScanner

WEBAPP_URL = "https://script.google.com/macros/s/AKfycbxNJ1SUfWchtcLxM0aVCLx_NsPyA9rVeBe6UfKKT70DXQlweVyzMdi1bzb8VVS-VqxhhQ/exec"
SECRET = os.environ.get("FARMHUT_SECRET", "CHANGE-ME")

SENSORS = [
    {"mac": "A4:C1:38:6A:77:BE", "label": "내부1층"},
    {"mac": "A4:C1:38:B4:5E:13", "label": "바닥외부"},
    {"mac": "A4:C1:38:32:8C:21", "label": "다락"},
]

SCAN_SEC = 8.0   # 최대 대기. 3개 다 찾으면 즉시 중단(조기 종료)
C_CUR = "ebe0ccc1-7a0a-4b0c-8a1a-6ff2997da3a6"   # 현재값: temp(i16/100) humi(u8) volt(u16/1000)


def decode_current(b):
    return (struct.unpack_from("<h", b, 0)[0] / 100.0, b[2], struct.unpack_from("<H", b, 3)[0] / 1000.0)


async def scan_for(macs, timeout):
    """대상 MAC 들을 스캔. 모두 찾으면 즉시 반환(조기 종료) → 가까우면 1~2초."""
    targets = {m.upper() for m in macs}
    found = {}
    done = asyncio.Event()

    def cb(dev, adv):
        a = (dev.address or "").upper()
        if a in targets and a not in found:
            found[a] = dev
            if len(found) == len(targets):
                done.set()

    scanner = BleakScanner(detection_callback=cb)
    await scanner.start()
    try:
        await asyncio.wait_for(done.wait(), timeout)
    except asyncio.TimeoutError:
        pass
    finally:
        await scanner.stop()
    return found


async def read_current(dev):
    async with BleakClient(dev) as c:
        v = await c.read_gatt_char(C_CUR)
    return decode_current(v)


def post_current(readings):
    body = json.dumps({"secret": SECRET, "mode": "current", "readings": readings}).encode("utf-8")
    req = urllib.request.Request(WEBAPP_URL, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


async def main():
    ap = argparse.ArgumentParser(description="세 센서 현재값만 빠르게 업데이트")
    ap.add_argument("--dry-run", action="store_true", help="업로드 없이 읽기+시간측정")
    args = ap.parse_args()

    if not args.dry_run and SECRET in ("", "CHANGE-ME"):
        print("[중단] 환경변수 FARMHUT_SECRET 를 설정하세요. (검증만: --dry-run)")
        sys.exit(2)

    t0 = time.perf_counter()
    print(f"[스캔] 최대 {SCAN_SEC:.0f}초 (3개 다 찾으면 즉시 중단)…")
    by_mac = await scan_for([s["mac"] for s in SENSORS], SCAN_SEC)
    t_scan = time.perf_counter() - t0

    now_ms = time.time() * 1000
    now_iso = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    readings = []
    print()
    for s in SENSORS:
        dev = by_mac.get(s["mac"].upper())
        if dev is None:
            print(f"  [{s['label']}] 못 찾음(범위/전원)")
            continue
        ts = time.perf_counter()
        try:
            temp, hum, volt = await read_current(dev)
            dt = time.perf_counter() - ts
            print(f"  [{s['label']}] {temp:.1f}℃ / {hum}% · {volt:.2f}V   (읽기 {dt:.1f}s)")
            readings.append({"sensor": s["label"], "temp": round(temp, 1), "hum": hum,
                             "volt": round(volt, 3), "time_ms": now_ms})
        except Exception as e:
            print(f"  [{s['label']}] 오류: {e}")

    t_up = 0.0
    if readings and not args.dry_run:
        up0 = time.perf_counter()
        try:
            res = post_current(readings)
            t_up = time.perf_counter() - up0
            print(f"\n[업로드] {res.get('updated')}개 갱신 ({t_up:.1f}s)" if res.get("ok")
                  else f"\n[업로드 거부] {res.get('error')}")
        except Exception as e:
            print(f"\n[업로드 오류] {e}")
    elif args.dry_run:
        print(f"\n(dry-run) 현재시각 {now_iso} · 업로드 안 함")

    total = time.perf_counter() - t0
    print(f"\n=== 소요시간: 스캔 {t_scan:.1f}s"
          + (f" · 업로드 {t_up:.1f}s" if t_up else "")
          + f" · 전체 {total:.1f}s · 읽음 {len(readings)}/{len(SENSORS)} ===")
    sys.exit(0 if len(readings) == len(SENSORS) else 1)


if __name__ == "__main__":
    asyncio.run(main())
