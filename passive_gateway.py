#!/usr/bin/env python3
"""
passive_gateway.py — 라즈베리파이 상주 게이트웨이.

평소: 비콘(MiBeacon/FE95)을 '연결 없이' 계속 수신 → 복호화 → '농막_현재' 주기 갱신.
주기(기본 1시간): 각 센서에 '연결'해 이력을 '시트 마지막 이후'만 받아 '농막_시간별'에 올림
                 → Pi가 꺼져 있던 공백도 다음 연결에서 자동 백필(기기 ~5개월 버퍼 내).
오류/BLE 끊김: 자동 재시작. systemd로 부팅 자동시작·자동복구(아래 .service 참고).

필요: pip install bleak cryptography
설정: sensor_keys.json(bindkey, gitignore), 환경변수 FARMHUT_SECRET
"""
import asyncio
import json
import os
import struct
import sys
import time
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from bleak import BleakScanner, BleakClient
from bleak.exc import BleakError
from cryptography.hazmat.primitives.ciphers.aead import AESCCM

# ── 설정 ───────────────────────────────────────────────
WEBAPP_URL = "https://script.google.com/macros/s/AKfycbzEJn5u4lIAJprA69xR_yRSEeRmk2I_qe_igsAlPqEq1umHdsNkykOcF_imW3Zj5s_Rzg/exec"
SECRET = os.environ.get("FARMHUT_SECRET", "CHANGE-ME")
CFG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sensor_keys.json")

CURRENT_INTERVAL = float(os.environ.get("GW_CURRENT_INTERVAL", 600))    # '농막_현재' 갱신 주기(초)
HISTORY_INTERVAL = float(os.environ.get("GW_HISTORY_INTERVAL", 3600))   # 이력 동기화 주기(초)
SCAN_TIMEOUT = 15.0
IDLE_TIMEOUT = 10.0
OVERLAP = 2

# ── GATT ───────────────────────────────────────────────
FE95 = "0000fe95-0000-1000-8000-00805f9b34fb"
SERVICE = "ebe0ccb0-7a0a-4b0c-8a1a-6ff2997da3a6"
C_TIME = "ebe0ccb7-7a0a-4b0c-8a1a-6ff2997da3a6"
C_LATEST = "ebe0ccbb-7a0a-4b0c-8a1a-6ff2997da3a6"
C_INDEX = "ebe0ccba-7a0a-4b0c-8a1a-6ff2997da3a6"
C_HIST = "ebe0ccbc-7a0a-4b0c-8a1a-6ff2997da3a6"


def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def load_cfg():
    with open(CFG, encoding="utf-8") as f:
        return json.load(f)


# ── 비콘 복호화(현재값) ─────────────────────────────────
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
    ct = bytes(sd[i:-7])
    if not ct:
        return None
    try:
        return AESCCM(key, tag_length=4).decrypt(nonce, ct + bytes(sd[-4:]), b"\x11")
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
            t, h = struct.unpack("<hH", val); out["temp"] = t / 10; out["humi"] = h / 10
        elif oid == 0x100A and ln == 1:
            out["batt"] = val[0]
        idx += 3 + ln
    return out


# ── 업로드/조회(Apps Script) ────────────────────────────
def _post(payload):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(WEBAPP_URL, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def post_current(readings):
    return _post({"secret": SECRET, "mode": "current", "readings": readings})


def post_history(label, boot_epoch, rows):
    return _post({"secret": SECRET, "sensor": label, "bootEpoch": boot_epoch, "records": rows})


def get_sheet_last():
    try:
        with urllib.request.urlopen(WEBAPP_URL, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        log(f"[시트] 마지막시각 조회 실패({e})")
        return {}
    import datetime
    out = {}
    for label, info in (data.get("bySensor") or {}).items():
        last = info.get("last")
        if not last:
            continue
        for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
            try:
                out[label] = datetime.datetime.strptime(last, fmt).replace(
                    tzinfo=datetime.timezone.utc).timestamp() * 1000
                break
            except ValueError:
                pass
    return out


# ── 이력 동기화(연결) ───────────────────────────────────
def decode_record(b):
    idx, ts, maxt, maxh, mint, minh = struct.unpack_from("<IIhBhB", b, 0)
    return {"idx": idx, "ts": ts, "maxT": maxt / 10, "maxH": maxh, "minT": mint / 10, "minH": minh}


def iso_hour(ms):
    import datetime
    return datetime.datetime.utcfromtimestamp(ms / 1000).strftime("%Y-%m-%dT%H:%M:%S.000Z")


async def sync_history_one(mac, label, sheet_last_ms):
    dev = await BleakScanner.find_device_by_address(mac, timeout=SCAN_TIMEOUT)
    if dev is None:
        return f"[{label}] 이력: 못 찾음"
    async with BleakClient(dev, services=[SERVICE]) as client:
        counter_s = struct.unpack_from("<I", await client.read_gatt_char(C_TIME), 0)[0]
        now_ms = time.time() * 1000
        boot_epoch = round(now_ms / 1000 - counter_s)
        latest = decode_record(await client.read_gatt_char(C_LATEST))

        start_idx = 0
        last_ms = sheet_last_ms.get(label)
        if last_ms:
            idx_of_last = (counter_s - (now_ms - last_ms) / 1000) / 3600 - 1
            start_idx = max(0, round(idx_of_last) - OVERLAP)
        start_idx = min(start_idx, latest["idx"] + 1)
        if start_idx > latest["idx"]:
            return f"[{label}] 이력: 새 레코드 없음"

        await client.write_gatt_char(C_INDEX, struct.pack("<I", start_idx), response=True)
        records = []
        done = asyncio.Event()
        loop = asyncio.get_event_loop()
        st = {"idle": None}

        def kick():
            if st["idle"]:
                st["idle"].cancel()
            st["idle"] = loop.call_later(IDLE_TIMEOUT, done.set)

        def on_hist(_s, data):
            if len(data) >= 14:
                r = decode_record(data)
                records.append(r)
                if r["idx"] >= latest["idx"]:
                    done.set()
                else:
                    kick()

        kick()
        await client.start_notify(C_HIST, on_hist)
        await done.wait()
        await client.stop_notify(C_HIST)

    rows = []
    for r in records:
        hour_ms = round((now_ms - (counter_s - r["ts"]) * 1000) / 3600000) * 3600000
        rows.append({"idx": r["idx"], "time": iso_hour(hour_ms),
                     "maxT": r["maxT"], "minT": r["minT"], "maxH": r["maxH"], "minH": r["minH"]})
    rows.sort(key=lambda x: x["idx"])
    if not rows:
        return f"[{label}] 이력: 받은 레코드 없음"
    res = post_history(label, boot_epoch, rows)
    return f"[{label}] 이력: {len(rows)}개 → 추가 {res.get('added')}/중복 {res.get('skipped')}"


# ── 메인 루프 ───────────────────────────────────────────
async def current_uploader(cfg, latest):
    while True:
        await asyncio.sleep(CURRENT_INTERVAL)
        now_ms = time.time() * 1000
        readings = []
        for label in cfg:
            v = latest[label]
            if "temp" in v or "humi" in v:
                r = {"sensor": label, "time_ms": now_ms}
                if "temp" in v: r["temp"] = round(v["temp"], 1)
                if "humi" in v: r["hum"] = v["humi"]
                if "batt" in v: r["volt"] = round(2.2 + 0.9 * v["batt"] / 100, 3)
                readings.append(r)
        if readings:
            try:
                res = post_current(readings)
                log(f"[현재] {res.get('updated')}개 갱신")
            except Exception as e:
                log(f"[현재] 업로드 오류: {e}")


async def history_syncer(cfg):
    # 시작 직후 1회 + 이후 주기적으로
    while True:
        try:
            sheet_last = get_sheet_last()
            for label, v in cfg.items():
                try:
                    msg = await sync_history_one(v["mac"], label, sheet_last)
                    log(msg)
                except BleakError as e:
                    log(f"[{label}] 이력 연결오류: {e}")
                except Exception as e:
                    log(f"[{label}] 이력 오류: {e}")
                await asyncio.sleep(2)   # 센서 간 간격
        except Exception as e:
            log(f"[이력] 라운드 오류: {e}")
        await asyncio.sleep(HISTORY_INTERVAL)


async def passive_loop(cfg, latest):
    by_mac = {v["mac"].upper(): (label, bytes.fromhex(v["key"])) for label, v in cfg.items()}

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
        if vals:
            vals["ts"] = time.time()
            latest[label].update(vals)

    while True:  # 스캐너가 죽으면 재시작
        try:
            scanner = BleakScanner(detection_callback=cb)
            await scanner.start()
            log("[passive] 비콘 수신 시작")
            while True:
                await asyncio.sleep(3600)   # 유지(콜백이 백그라운드로 동작)
        except Exception as e:
            log(f"[passive] 스캐너 오류, 10초 후 재시작: {e}")
            try:
                await scanner.stop()
            except Exception:
                pass
            await asyncio.sleep(10)


async def main():
    if SECRET in ("", "CHANGE-ME"):
        log("[중단] 환경변수 FARMHUT_SECRET 를 설정하세요.")
        sys.exit(2)
    cfg = load_cfg()
    latest = {label: {} for label in cfg}
    log(f"게이트웨이 시작 (현재 {CURRENT_INTERVAL:.0f}s / 이력 {HISTORY_INTERVAL:.0f}s 주기)")
    await asyncio.gather(
        passive_loop(cfg, latest),
        current_uploader(cfg, latest),
        history_syncer(cfg),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
