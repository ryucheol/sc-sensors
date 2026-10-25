#!/usr/bin/env python3
"""
passive_gateway.py — 라즈베리파이 상주 게이트웨이.

현재값: 비콘(MiBeacon/FE95)을 '연결 없이' 계속 수신 → 복호화 → '농막_현재' 주기 갱신.
평균:   수신한 passive 샘플로 '시간가중 평균'을 매 정시 '농막_평균'(파생값)에 올림 — 연결 불필요.
이력:   각 센서에 '연결'해 '시트 마지막 이후'만 받아 '농막_시간별'(권위 min/max)에 올림.
        배터리 절약을 위해 '시작 시 1회 + 하루 1회'만 연결(기기 ~5개월 버퍼라 공백은 자동 백필).
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
HISTORY_INTERVAL = float(os.environ.get("GW_HISTORY_INTERVAL", 86400))  # 이력 동기화(연결) 주기: 시작 시 1회 + 하루 1회
AVG_BUCKET = float(os.environ.get("GW_AVG_BUCKET", 3600))               # 시간별 평균 집계 단위(초)=1시간
AVG_CHECK = 20.0                                                        # 버킷 경계 점검 간격(초)
BEACON_LOG = os.environ.get("GW_BEACON_LOG", "change").lower()          # 수신 로그: change(값 바뀔 때만, 기본)|all(반복까지 모두)|off
SCAN_TIMEOUT = 15.0
IDLE_TIMEOUT = 10.0
OVERLAP = 2
HISTORY_RETRY_ROUNDS = 3        # 이력 연결 실패 시 재시도 라운드 수(하루 간격이라 실패가 비쌈)
HISTORY_RETRY_BACKOFF = 120.0   # 실패 센서 재시도 간 대기(초)


# ── 시간가중 평균(passive 샘플 → 버킷 평균) ─────────────
class TWAvg:
    """한 버킷(예: 1시간) 동안 '값×지속시간'을 누적해 시간가중 평균을 낸다.
    on-change 방송이라 값이 바뀔 때까지 같은 값이 유지되므로, 단순평균보다 정확."""
    __slots__ = ("val", "t", "wsum", "secs", "touched")

    def __init__(self):
        self.val = None; self.t = None; self.wsum = 0.0; self.secs = 0.0; self.touched = False

    def update(self, val, now):
        if self.val is not None and self.t is not None and now > self.t:
            self.wsum += self.val * (now - self.t); self.secs += now - self.t
        self.val = val; self.t = now; self.touched = True

    def flush(self, bucket_end):
        """버킷 끝까지 마지막 값을 누적하고 평균 반환 후 리셋(값은 다음 버킷으로 이월)."""
        if self.val is not None and self.t is not None and bucket_end > self.t:
            self.wsum += self.val * (bucket_end - self.t); self.secs += bucket_end - self.t
            self.t = bucket_end
        avg = (self.wsum / self.secs) if (self.secs > 0 and self.touched) else None
        self.wsum = 0.0; self.secs = 0.0; self.touched = False
        return avg

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


def post_avg(readings):
    return _post({"secret": SECRET, "mode": "avg", "readings": readings})


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
        raise RuntimeError("스캔 실패(못 찾음)")
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
    # 시작 직후 1회(권위 데이터 확보·공백 백필) + 이후 주기적(기본 하루 1회).
    # 하루 간격이라 실패가 비싸므로, 실패한 센서만 몇 라운드 재시도 후 긴 대기.
    while True:
        pending = dict(cfg)
        for rnd in range(HISTORY_RETRY_ROUNDS):
            if not pending:
                break
            if rnd:
                log(f"[이력] 실패 {len(pending)}개 재시도 {rnd}/{HISTORY_RETRY_ROUNDS - 1} ({HISTORY_RETRY_BACKOFF:.0f}s 후)")
                await asyncio.sleep(HISTORY_RETRY_BACKOFF)
            try:
                sheet_last = get_sheet_last()
            except Exception as e:
                log(f"[이력] 시트 조회 오류: {e}")
                sheet_last = {}
            failed = {}
            for label, v in pending.items():
                try:
                    log(await sync_history_one(v["mac"], label, sheet_last))
                except BleakError as e:
                    log(f"[{label}] 이력 연결오류: {e}"); failed[label] = v
                except Exception as e:
                    log(f"[{label}] 이력 오류: {e}"); failed[label] = v
                await asyncio.sleep(2)   # 센서 간 간격
            pending = failed
        if pending:
            log(f"[이력] 최종 실패: {list(pending)} — 다음 주기에 재시도")
        await asyncio.sleep(HISTORY_INTERVAL)


async def avg_uploader(cfg, avg_state):
    """버킷(기본 1시간) 경계가 지나면 직전 버킷의 시간가중 평균을 '농막_평균'에 올림.
    - 시작 당시 '진행 중'이던 버킷은 일부만 관측되므로 업로드하지 않고 버퍼만 비운다.
    - 평균은 재계산 불가 → 업로드 실패분은 pending에 쌓아 다음 성공 때 함께 보냄(서버가 중복 제거).
    - 1시간 미만 버킷(테스트용)은 (센서|정시) 중복키가 충돌하므로 업로드하지 않고 로그만 남긴다."""
    start_ts = time.time()
    last_bucket = int(start_ts // AVG_BUCKET)
    can_upload = AVG_BUCKET >= 3600
    if not can_upload:
        log(f"[평균] 버킷 {AVG_BUCKET:.0f}s < 1h → 테스트모드(로그만, 업로드 안 함)")
    pending = []
    while True:
        await asyncio.sleep(AVG_CHECK)
        b = int(time.time() // AVG_BUCKET)
        if b <= last_bucket:
            continue
        bucket_end = b * AVG_BUCKET                  # 완료된 버킷의 끝 = 현재 버킷의 시작
        full = (last_bucket * AVG_BUCKET) >= start_ts  # 버킷 전체를 관측했는가(부분 버킷 제외)
        batch = []
        for label in cfg:
            st = avg_state[label]
            at = st["temp"].flush(bucket_end)
            ah = st["humi"].flush(bucket_end)
            if not full or (at is None and ah is None):
                continue
            r = {"sensor": label, "time_ms": last_bucket * AVG_BUCKET * 1000}
            if at is not None: r["temp"] = round(at, 1)
            if ah is not None: r["hum"] = round(ah, 1)
            batch.append(r)
        last_bucket = b
        if batch:
            hhmm = time.strftime("%H:%M", time.localtime(batch[0]["time_ms"] / 1000))
            detail = "  ".join(f"{r['sensor']} {r.get('temp', '-')}℃/{r.get('hum', '-')}%" for r in batch)
            log(f"[평균] {hhmm}  {detail}")
        if not can_upload:
            continue
        pending.extend(batch)
        pending = pending[-1000:]       # 안전 상한(연결 장기불통 대비)
        if not pending:
            continue
        try:
            res = post_avg(pending)
            if res.get("mode") != "avg":
                log("[평균] ⚠ Code.gs 재배포 필요(mode:avg 미지원) — 보류 후 재시도")
            else:
                log(f"[평균] 업로드 추가 {res.get('added')}/중복 {res.get('skipped')} (보류분 {len(pending)})")
                pending = []
        except Exception as e:
            log(f"[평균] 업로드 오류(보류 {len(pending)}개): {e}")


async def passive_loop(cfg, latest, avg_state):
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
        if not vals:
            return
        now = time.time()
        prev = latest[label]
        st = avg_state[label]
        parts = []
        is_change = False
        if "temp" in vals:
            st["temp"].update(vals["temp"], now)
            if "temp" not in prev or vals["temp"] != prev["temp"]:
                d = (vals["temp"] - prev["temp"]) if "temp" in prev else 0.0
                parts.append(f"T {vals['temp']:.1f}℃({d:+.1f})*"); is_change = True
            else:
                parts.append(f"T {vals['temp']:.1f}℃")
        if "humi" in vals:
            st["humi"].update(vals["humi"], now)
            if "humi" not in prev or vals["humi"] != prev["humi"]:
                d = (vals["humi"] - prev["humi"]) if "humi" in prev else 0.0
                parts.append(f"H {vals['humi']:.1f}%({d:+.1f})*"); is_change = True
            else:
                parts.append(f"H {vals['humi']:.1f}%")
        if "batt" in vals:
            parts.append(f"bat {vals['batt']}%")
        vals["ts"] = now
        prev.update(vals)
        if BEACON_LOG == "all" or (BEACON_LOG == "change" and is_change):
            log(f"[수신] {label}  {'  '.join(parts)}  rssi {adv.rssi}")

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
    avg_state = {label: {"temp": TWAvg(), "humi": TWAvg()} for label in cfg}
    log(f"게이트웨이 시작 (현재 {CURRENT_INTERVAL:.0f}s / 평균 {AVG_BUCKET:.0f}s / 이력 {HISTORY_INTERVAL:.0f}s)")
    await asyncio.gather(
        passive_loop(cfg, latest, avg_state),
        current_uploader(cfg, latest),
        avg_uploader(cfg, avg_state),
        history_syncer(cfg),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
