#!/usr/bin/env python3
"""
farmhut_sync.py — PC에서 명령 하나로 농막 샤오미 센서 3개를 모두 구글시트에 증분 동기화.

사용:
    python farmhut_sync.py              # 전체 자동 동기화 (사용자 입력 없음)
    python farmhut_sync.py --dry-run    # 시트에 올리지 않고 받을 내용만 출력(검증용)

준비물:
    - bleak 설치:  python -m pip install bleak
    - 아래 SECRET 를 Code.gs 의 SECRET 과 동일하게 설정 (또는 환경변수 FARMHUT_SECRET)

동작:
    1) 웹앱 doGet(JSON)에서 센서별 시트 마지막 시각을 읽음
    2) 각 센서(MAC)로 연결 → 그 시각 이후 인덱스부터만 이력 스트림(ccba seek)
    3) 받은 시간별 최고/최저(온·습도)를 웹앱 doPost 로 업로드(서버가 중복 제거)
    PC는 MAC 을 직접 보므로 선택창/LCD 대조 없이 완전 자동.
"""

import argparse
import asyncio
import json
import os
import struct
import sys
import time
import urllib.request
import urllib.error

sys.stdout.reconfigure(encoding="utf-8")

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

# ─────────────── 설정 ───────────────
WEBAPP_URL = "https://script.google.com/macros/s/AKfycbzEJn5u4lIAJprA69xR_yRSEeRmk2I_qe_igsAlPqEq1umHdsNkykOcF_imW3Zj5s_Rzg/exec"
SECRET = os.environ.get("FARMHUT_SECRET", "CHANGE-ME")   # ← Code.gs 의 SECRET 과 동일하게 (또는 환경변수)

SENSORS = [
    {"mac": "A4:C1:38:6A:77:BE", "label": "내부1층"},   # 끝자리 BE
    {"mac": "A4:C1:38:B4:5E:13", "label": "바닥외부"},   # 끝자리 13
    {"mac": "A4:C1:38:32:8C:21", "label": "다락"},       # 끝자리 21
]

SCAN_TIMEOUT = 12.0
IDLE_TIMEOUT = 10.0
MAX_ROUNDS = 3       # 실패한 센서만 최대 몇 라운드까지 재시도할지
OVERLAP = 2          # 시트 마지막 시각보다 몇 시간 겹쳐 받을지(서버가 중복 제거)
INTER_SENSOR = 2.0   # 센서 간 연결 간격(초) - BLE 어댑터 회복용(연속 연결 실패 감소)

# ─────────────── GATT ───────────────
SERVICE  = "ebe0ccb0-7a0a-4b0c-8a1a-6ff2997da3a6"
C_TIME   = "ebe0ccb7-7a0a-4b0c-8a1a-6ff2997da3a6"   # u32: 부팅 후 초
C_LATEST = "ebe0ccbb-7a0a-4b0c-8a1a-6ff2997da3a6"   # 최신 이력 레코드
C_INDEX  = "ebe0ccba-7a0a-4b0c-8a1a-6ff2997da3a6"   # 시작 인덱스 포인터(seek)
C_HIST   = "ebe0ccbc-7a0a-4b0c-8a1a-6ff2997da3a6"   # 이력 notify
C_CUR    = "ebe0ccc1-7a0a-4b0c-8a1a-6ff2997da3a6"   # 현재값(참고)


def decode_record(b):
    idx, ts, maxt, maxh, mint, minh = struct.unpack_from("<IIhBhB", b, 0)
    return {"idx": idx, "ts": ts, "maxT": maxt / 10, "maxH": maxh, "minT": mint / 10, "minH": minh}


def iso_hour(ms):
    import datetime
    return datetime.datetime.utcfromtimestamp(ms / 1000).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def get_sheet_last():
    """웹앱 doGet(JSON) → {label: last_ms}."""
    try:
        with urllib.request.urlopen(WEBAPP_URL, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        print(f"[경고] 시트 마지막시각 조회 실패({e}) → 전체(full)로 받습니다.")
        return {}
    out = {}
    for label, info in (data.get("bySensor") or {}).items():
        last = info.get("last")
        if last:
            import datetime
            try:
                out[label] = datetime.datetime.strptime(last, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
                    tzinfo=datetime.timezone.utc).timestamp() * 1000
            except ValueError:
                out[label] = datetime.datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=datetime.timezone.utc).timestamp() * 1000
    return out


def post_rows(label, boot_epoch, rows):
    """웹앱 doPost 로 업로드 → {ok, added, skipped} 반환(PC는 응답 읽힘)."""
    body = json.dumps({"secret": SECRET, "sensor": label, "bootEpoch": boot_epoch, "records": rows}).encode("utf-8")
    req = urllib.request.Request(WEBAPP_URL, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


async def sync_sensor(sensor, sheet_last_ms, dry_run):
    mac, label = sensor["mac"], sensor["label"]
    dev = await BleakScanner.find_device_by_address(mac, timeout=SCAN_TIMEOUT)
    if dev is None:
        return (False, f"[{label}] 못 찾음(전원/범위 확인)")

    async with BleakClient(dev, services=[SERVICE]) as client:   # 서비스 하나만 탐색 → 연결 단축
        tv = await client.read_gatt_char(C_TIME)
        counter_s = struct.unpack_from("<I", tv, 0)[0]
        now_ms = time.time() * 1000
        boot_epoch = round(now_ms / 1000 - counter_s)
        latest = decode_record(await client.read_gatt_char(C_LATEST))
        try:
            cv = await client.read_gatt_char(C_CUR)
            cur = f"{struct.unpack_from('<h', cv, 0)[0] / 100:.1f}℃/{cv[2]}%"
        except Exception:
            cur = "?"

        # 시작 인덱스: 시트 마지막 시각 기반(없으면 전체)
        start_idx = 0
        src = "전체"
        last_ms = sheet_last_ms.get(label)
        if last_ms:
            idx_of_last = (counter_s - (now_ms - last_ms) / 1000) / 3600 - 1
            start_idx = max(0, round(idx_of_last) - OVERLAP)
            src = "시트 이후"
        start_idx = min(start_idx, latest["idx"] + 1)

        if start_idx > latest["idx"]:
            return (True, f"[{label}] 현재 {cur} · 새 레코드 없음 (최신 idx={latest['idx']})")

        # seek 후 스트림 수집
        await client.write_gatt_char(C_INDEX, struct.pack("<I", start_idx), response=True)
        records = []
        done = asyncio.Event()
        loop = asyncio.get_event_loop()
        state = {"idle": None, "max": start_idx - 1}

        def kick():
            if state["idle"]:
                state["idle"].cancel()
            state["idle"] = loop.call_later(IDLE_TIMEOUT, done.set)

        def on_hist(_s, data):
            if len(data) >= 14:
                r = decode_record(data)
                records.append(r)
                if r["idx"] > state["max"]:
                    state["max"] = r["idx"]
                if r["idx"] >= latest["idx"]:
                    done.set()
                else:
                    kick()

        kick()
        await client.start_notify(C_HIST, on_hist)
        await done.wait()
        await client.stop_notify(C_HIST)

    # 절대 시각 환산 + 행 구성
    rows = []
    for r in records:
        wall_end_ms = now_ms - (counter_s - r["ts"]) * 1000
        hour_ms = round(wall_end_ms / 3600000) * 3600000
        rows.append({"idx": r["idx"], "time": iso_hour(hour_ms),
                     "maxT": r["maxT"], "minT": r["minT"], "maxH": r["maxH"], "minH": r["minH"]})
    rows.sort(key=lambda x: x["idx"])
    if not rows:
        return (True, f"[{label}] 현재 {cur} · 받은 새 레코드 없음")

    span = f"{rows[0]['time'][:16]} ~ {rows[-1]['time'][:16]}"
    if dry_run:
        return (True, f"[{label}] 현재 {cur} · (dry-run) {len(rows)}개 수집 [{src}] ({span}) — 업로드 안 함")

    res = post_rows(label, boot_epoch, rows)
    if res.get("ok"):
        return (True, f"[{label}] 현재 {cur} · {len(rows)}개 수집 [{src}] → 추가 {res.get('added')} / 중복 {res.get('skipped')}")
    return (False, f"[{label}] 업로드 거부: {res.get('error')} (SECRET 확인)")


async def attempt_sensor(sensor, sheet_last_ms, dry_run):
    """센서 1회 시도. 예외도 삼켜 (ok, msg) 로 반환."""
    try:
        return await sync_sensor(sensor, sheet_last_ms, dry_run)
    except BleakError as e:
        return (False, f"[{sensor['label']}] 연결오류: {e}")
    except Exception as e:
        return (False, f"[{sensor['label']}] 오류: {e}")


async def main():
    ap = argparse.ArgumentParser(description="농막 샤오미 센서 3개 자동 동기화")
    ap.add_argument("--dry-run", action="store_true", help="시트에 올리지 않고 받을 내용만 출력")
    args = ap.parse_args()

    if not args.dry_run and SECRET in ("", "CHANGE-ME"):
        print("[중단] SECRET 이 설정되지 않았습니다. 환경변수 FARMHUT_SECRET 를 설정하세요(run_sync.bat 이 물어봅니다).")
        print("       (검증만 하려면 --dry-run)")
        sys.exit(2)

    print("=== 농막 센서 자동 동기화 시작 ===")
    sheet_last_ms = get_sheet_last()
    if sheet_last_ms:
        print("[시트] 센서별 마지막 시각:", {k: iso_hour(v)[:16] for k, v in sheet_last_ms.items()})
    else:
        print("[시트] 기존 데이터 없음 → 전체 수집")

    # 실패한 센서만 다음 라운드에서 재시도 (최대 MAX_ROUNDS 라운드)
    results = {s["label"]: (False, f"[{s['label']}] 미시도") for s in SENSORS}
    pending = list(SENSORS)
    for rnd in range(1, MAX_ROUNDS + 1):
        if not pending:
            break
        if rnd > 1:
            print(f"\n=== 재시도 라운드 {rnd}/{MAX_ROUNDS} — 실패 센서 {len(pending)}개 ===")
            await asyncio.sleep(3)
        still = []
        for i, s in enumerate(pending):
            if i > 0:
                await asyncio.sleep(INTER_SENSOR)   # 센서 간 간격(어댑터 회복)
            print(f"\n--- {s['label']} ({s['mac']})"
                  + (f"  [라운드 {rnd}]" if rnd > 1 else "") + " ---")
            ok, msg = await attempt_sensor(s, sheet_last_ms, args.dry_run)
            print("  " + msg)
            results[s["label"]] = (ok, msg)
            if not ok:
                still.append(s)
        pending = still

    print("\n=== 요약 ===")
    for s in SENSORS:
        print("  " + results[s["label"]][1])

    any_fail = any(not results[s["label"]][0] for s in SENSORS)
    sys.exit(1 if any_fail else 0)


if __name__ == "__main__":
    asyncio.run(main())
