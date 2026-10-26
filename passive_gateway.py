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
import socket
import sqlite3
import struct
import subprocess
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
_HERE = os.path.dirname(os.path.abspath(__file__))
# 기본값 = 농막. 두 번째 인스턴스(예: 선반)는 아래 3개를 env로 '함께' 지정한다.
#   GW_WEBAPP_URL(대상 시트) · GW_KEYS_FILE(그 센서 bindkey) · GW_QUEUE_DB(독립 큐)
# 셋 중 일부만 주면 main()에서 중단 → 선반 값이 농막 시트로 잘못 올라가는 사고 방지.
WEBAPP_URL = os.environ.get("GW_WEBAPP_URL") or "https://script.google.com/macros/s/AKfycbzEJn5u4lIAJprA69xR_yRSEeRmk2I_qe_igsAlPqEq1umHdsNkykOcF_imW3Zj5s_Rzg/exec"
SECRET = os.environ.get("FARMHUT_SECRET", "CHANGE-ME")
CFG = os.environ.get("GW_KEYS_FILE") or os.path.join(_HERE, "sensor_keys.json")
QUEUE_DB = os.environ.get("GW_QUEUE_DB") or os.path.join(_HERE, "gateway_queue.db")  # 평균 store-and-forward(오프라인 대비)

CURRENT_INTERVAL = float(os.environ.get("GW_CURRENT_INTERVAL", 600))    # '농막_현재' 하트비트 주기(초): 변화 없어도 최소 이 간격마다 1회
CURRENT_COOLDOWN = float(os.environ.get("GW_CURRENT_COOLDOWN", 60))     # 변화 시 업로드 최소 간격(초) — 잦은 쓰기 방지
HISTORY_INTERVAL = float(os.environ.get("GW_HISTORY_INTERVAL", 86400))  # 이력 동기화(연결) 주기: 시작 시 1회 + 하루 1회
AVG_BUCKET = float(os.environ.get("GW_AVG_BUCKET", 3600))               # 시간별 평균 집계 단위(초)=1시간
AVG_CHECK = 20.0                                                        # 버킷 경계 점검 간격(초)
BEACON_LOG = os.environ.get("GW_BEACON_LOG", "change").lower()          # 수신 로그: change(값 바뀔 때만, 기본)|all(반복까지 모두)|off
HUMI_DEADBAND = float(os.environ.get("GW_HUMI_DEADBAND", 1.0))          # 습도 데드밴드(%): 마지막 '변화'값 대비 이 미만이면 변화로 안 침(로그·업로드 트리거만. 평균은 전 샘플 사용)
MONITOR = os.environ.get("GW_MONITOR", "0").lower() in ("1", "true", "yes", "on")   # 모니터(읽기전용): 수신 로그만, 서버 업로드·센서 연결 없음
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


_mid_dots = False   # 점(.)이 이어지는 줄이 진행 중인지


def log(msg):
    """일반 로그 — 진행 중인 '점 줄'이 있으면 먼저 개행으로 닫는다."""
    global _mid_dots
    pre = "\n" if _mid_dots else ""
    _mid_dots = False
    print(f"{pre}[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def log_open(msg):
    """'점 줄' 시작: 타임스탬프+메시지를 개행 없이 출력(뒤에 점이 이어붙음)."""
    global _mid_dots
    pre = "\n" if _mid_dots else ""
    print(f"{pre}[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", end="", flush=True)
    _mid_dots = True


def mark(ch):
    """진행 중인 수신 줄에 표식 한 글자 추가(센서 번호: 바닥0 · 1층1 · 다락2)."""
    global _mid_dots
    print(ch, end="", flush=True)
    _mid_dots = True


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


def net_info():
    """현재 wlan 연결의 SSID와 IP (현재 탭 '갱신시각' 옆에 표시용). 실패 시 빈 문자열."""
    ip = ""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass
    ssid = ""
    try:
        # 활성 연결명(스캔 캐시와 무관하게 항상 확실) → 그 연결의 SSID
        conn = subprocess.run(["nmcli", "-g", "GENERAL.CONNECTION", "device", "show", "wlan0"],
                              capture_output=True, text=True, timeout=4).stdout.strip()
        if conn and conn != "--":
            ssid = subprocess.run(["nmcli", "-g", "802-11-wireless.ssid", "connection", "show", conn],
                                  capture_output=True, text=True, timeout=4).stdout.strip() or conn
    except Exception:
        pass
    return {"ssid": ssid, "ip": ip}


def post_current(readings, net=None):
    payload = {"secret": SECRET, "mode": "current", "readings": readings}
    if net:
        payload.update(net)   # ssid, ip
    return _post(payload)


def post_avg(readings):
    return _post({"secret": SECRET, "mode": "avg", "readings": readings})


def post_hist(readings):
    return _post({"secret": SECRET, "mode": "hist", "readings": readings})


def post_batt(readings):
    return _post({"secret": SECRET, "mode": "batt", "readings": readings})


# ── store-and-forward 로컬 큐 (오프라인/재부팅/정전에도 무손실) ──
# 농막은 인터넷이 방문 때만(폰 테더링). 모든 데이터를 로컬 SQLite에 영구 저장해 두고,
# 인터넷이 있을 때만 시트로 보낸다(센서엔 다시 안 묻는다).
#  - avg_q : passive 시간평균 → '농막_평균'
#  - hist_q: 시간별 min/max → '농막_시간별'. src 'p'(passive 근사)|'s'(센서 authoritative).
#            authoritative('s')가 오면 같은 시간의 passive('p') 값을 덮어쓴다(정확값 교정).
def q_connect():
    db = sqlite3.connect(QUEUE_DB)
    db.execute("CREATE TABLE IF NOT EXISTS avg_q ("
               "time_ms INTEGER, sensor TEXT, temp REAL, hum REAL, sent INTEGER DEFAULT 0, "
               "PRIMARY KEY(time_ms, sensor))")
    db.execute("CREATE TABLE IF NOT EXISTS hist_q ("
               "time_ms INTEGER, sensor TEXT, maxT REAL, minT REAL, maxH REAL, minH REAL, "
               "src TEXT, sent INTEGER DEFAULT 0, PRIMARY KEY(time_ms, sensor))")
    db.execute("CREATE TABLE IF NOT EXISTS batt_q ("    # 배터리 이력(수명 추세·예상 소진일용)
               "time_ms INTEGER, sensor TEXT, volt REAL, pct INTEGER, sent INTEGER DEFAULT 0, "
               "PRIMARY KEY(time_ms, sensor))")
    db.commit()
    return db


def q_store_avg(db, batch):
    for r in batch:
        db.execute("INSERT OR IGNORE INTO avg_q(time_ms, sensor, temp, hum) VALUES(?,?,?,?)",
                   (int(r["time_ms"]), r["sensor"], r.get("temp"), r.get("hum")))
    db.commit()


def q_store_batt(db, batch):
    for r in batch:
        db.execute("INSERT OR IGNORE INTO batt_q(time_ms, sensor, volt, pct) VALUES(?,?,?,?)",
                   (int(r["time_ms"]), r["sensor"], r.get("volt"), r.get("pct")))
    db.commit()


def q_store_hist(db, rows, src):
    # 's'(센서 authoritative): 기존(=passive) 행을 덮어쓰고 재전송 표시. 'p'(passive): 없을 때만 넣음.
    sql = ("INSERT OR REPLACE" if src == "s" else "INSERT OR IGNORE") + \
          " INTO hist_q(time_ms, sensor, maxT, minT, maxH, minH, src, sent) VALUES(?,?,?,?,?,?,?,0)"
    for r in rows:
        db.execute(sql, (int(r["time_ms"]), r["sensor"], r.get("maxT"), r.get("minT"),
                         r.get("maxH"), r.get("minH"), src))
    db.commit()


def q_last_sensor_ms(db, sensor):
    row = db.execute("SELECT MAX(time_ms) FROM hist_q WHERE sensor=? AND src='s'", (sensor,)).fetchone()
    return row[0] if row and row[0] is not None else None


def q_pending(db, table):
    return db.execute(f"SELECT COUNT(*) FROM {table} WHERE sent=0").fetchone()[0]


def online(timeout=3):
    # 시트 호스트에 빠르게 연결 시도 → 오프라인이면 즉시 False(긴 POST 타임아웃 회피).
    import socket
    try:
        socket.create_connection(("script.google.com", 443), timeout=timeout).close()
        return True
    except Exception:
        return False


def q_drain(db, chunk=300):
    """온라인일 때만 avg_q·hist_q 미전송분을 시트로 보낸다. 오프라인/실패면 로컬에 남겨둠."""
    if not online():
        return
    while True:   # 1) 평균 → 농막_평균
        rows = db.execute("SELECT time_ms, sensor, temp, hum FROM avg_q WHERE sent=0 "
                          "ORDER BY time_ms LIMIT ?", (chunk,)).fetchall()
        if not rows:
            break
        readings = [dict({"sensor": s, "time_ms": t},
                         **({"temp": tp} if tp is not None else {}),
                         **({"hum": h} if h is not None else {})) for t, s, tp, h in rows]
        try:
            res = post_avg(readings)
        except Exception as e:
            log(f"[평균] 업로드 오류(로컬 보관): {e}"); return
        if res.get("mode") != "avg":
            log("[평균] ⚠ Code.gs 재배포 필요(mode:avg) — 로컬 보관"); break
        db.executemany("UPDATE avg_q SET sent=1 WHERE time_ms=? AND sensor=?", [(t, s) for t, s, _, _ in rows])
        db.commit()
        log(f"[평균] 전송 {len(rows)} (남은대기 {q_pending(db, 'avg_q')})")
        if len(rows) < chunk:
            break
    while True:   # 2) min/max → 농막_시간별 (upsert)
        rows = db.execute("SELECT time_ms, sensor, maxT, minT, maxH, minH FROM hist_q WHERE sent=0 "
                          "ORDER BY time_ms LIMIT ?", (chunk,)).fetchall()
        if not rows:
            break
        readings = [{"sensor": s, "time_ms": t, "maxT": mxT, "minT": mnT, "maxH": mxH, "minH": mnH}
                    for t, s, mxT, mnT, mxH, mnH in rows]
        try:
            res = post_hist(readings)
        except Exception as e:
            log(f"[이력] 업로드 오류(로컬 보관): {e}"); return
        if res.get("mode") != "hist":
            log("[이력] ⚠ Code.gs 재배포 필요(mode:hist) — 로컬 보관"); break
        db.executemany("UPDATE hist_q SET sent=1 WHERE time_ms=? AND sensor=?", [(t, s) for t, s, *_ in rows])
        db.commit()
        log(f"[이력] 전송 {len(rows)} (추가 {res.get('added')}/갱신 {res.get('updated')}, 남은대기 {q_pending(db, 'hist_q')})")
        if len(rows) < chunk:
            break
    while True:   # 3) 배터리 → 사이트_배터리
        rows = db.execute("SELECT time_ms, sensor, volt, pct FROM batt_q WHERE sent=0 "
                          "ORDER BY time_ms LIMIT ?", (chunk,)).fetchall()
        if not rows:
            break
        readings = [{"sensor": s, "time_ms": t, "volt": v, "pct": p} for t, s, v, p in rows]
        try:
            res = post_batt(readings)
        except Exception as e:
            log(f"[배터리] 업로드 오류(로컬 보관): {e}"); return
        if res.get("mode") != "batt":
            log("[배터리] ⚠ Code.gs 재배포 필요(mode:batt) — 로컬 보관"); break
        db.executemany("UPDATE batt_q SET sent=1 WHERE time_ms=? AND sensor=?", [(t, s) for t, s, _, _ in rows])
        db.commit()
        log(f"[배터리] 전송 {len(rows)} (남은대기 {q_pending(db, 'batt_q')})")
        if len(rows) < chunk:
            break


def get_sheet_last():
    # 실패(오프라인 등)하면 예외를 던진다 → history_syncer가 온라인 판별에 사용.
    with urllib.request.urlopen(WEBAPP_URL, timeout=30) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
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


async def sync_history_one(mac, last_ms):
    # 센서 연결 → authoritative 시간별 min/max 레코드를 '.db 기준 증분'으로 가져온다.
    # last_ms = .db에 이미 있는 마지막 authoritative 시각(없으면 None → 전체). 반환: min/max rows.
    dev = await BleakScanner.find_device_by_address(mac, timeout=SCAN_TIMEOUT)
    if dev is None:
        raise RuntimeError("스캔 실패(못 찾음)")
    async with BleakClient(dev, services=[SERVICE]) as client:
        counter_s = struct.unpack_from("<I", await client.read_gatt_char(C_TIME), 0)[0]
        now_ms = time.time() * 1000
        latest = decode_record(await client.read_gatt_char(C_LATEST))

        start_idx = 0
        if last_ms:
            idx_of_last = (counter_s - (now_ms - last_ms) / 1000) / 3600 - 1
            start_idx = max(0, round(idx_of_last) - OVERLAP)
        start_idx = min(start_idx, latest["idx"] + 1)
        if start_idx > latest["idx"]:
            return []   # 새 레코드 없음

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
        rows.append({"time_ms": hour_ms, "maxT": r["maxT"], "minT": r["minT"],
                     "maxH": r["maxH"], "minH": r["minH"], "idx": r["idx"]})
    rows.sort(key=lambda x: x["idx"])
    return rows


# ── 메인 루프 ───────────────────────────────────────────
async def current_uploader(cfg, latest, dirty):
    """값이 바뀌면(dirty) 즉시 '농막_현재' 업로드하되, 연속 업로드는 최소 CURRENT_COOLDOWN(기본 30s) 간격.
    변화가 없어도 CURRENT_INTERVAL(기본 10분)마다 하트비트로 1회 올려 '갱신시각'을 살려둔다."""
    while True:
        try:
            await asyncio.wait_for(dirty.wait(), timeout=CURRENT_INTERVAL)
        except asyncio.TimeoutError:
            pass    # 변화 없음 → 하트비트
        dirty.clear()
        now_ms = time.time() * 1000
        readings = []
        for label in cfg:
            v = latest[label]
            if "temp" in v and "humi" in v:   # 온도·습도 둘 다 모인 뒤에만 업로드
                r = {"sensor": label, "time_ms": now_ms,
                     "temp": round(v["temp"], 1), "hum": v["humi"]}
                if "batt" in v: r["volt"] = round(2.2 + 0.9 * v["batt"] / 100, 3)
                readings.append(r)
        if readings and await asyncio.to_thread(online):   # 오프라인이면 아예 시도 안 함(현재값은 큐 안 함)
            try:
                net = await asyncio.to_thread(net_info)
                res = post_current(readings, net)
                log(f"[현재] {res.get('updated')}개 갱신 ({net.get('ssid','')} {net.get('ip','')})")
            except Exception as e:
                log(f"[현재] 업로드 오류: {e}")
        await asyncio.sleep(CURRENT_COOLDOWN)   # 쿨다운: 이 동안의 변화는 모아서 다음 1회로


async def history_syncer(cfg, box, lock, db):
    # 하루 1회 센서 연결 → authoritative 시간별 min/max를 '.db 기준 증분'으로 .db에 저장.
    # 인터넷 유무와 무관(오프라인에도 .db엔 쌓임) → 센서는 하루 1회만 접근(배터리 절약),
    # 같은 데이터는 재fetch 안 함. 업로드는 q_drain이 온라인일 때 따로 처리.
    # ★ BlueZ: 스캔 중 연결 불가 → 연결 구간에서만 passive 스캐너 정지(lock+box).
    while True:
        pending = dict(cfg)
        for rnd in range(HISTORY_RETRY_ROUNDS):
            if not pending:
                break
            if rnd:
                log(f"[이력] 실패 {len(pending)}개 재시도 {rnd}/{HISTORY_RETRY_ROUNDS - 1} ({HISTORY_RETRY_BACKOFF:.0f}s 후)")
                await asyncio.sleep(HISTORY_RETRY_BACKOFF)   # 대기 중엔 스캐너 가동
            failed = {}
            async with lock:                                 # ← 연결 구간: 스캐너 정지
                sc = box["scanner"]
                if sc is not None:
                    try: await sc.stop()
                    except Exception: pass
                    box["scanner"] = None   # 잠금 해제 후 passive_loop가 스캐너 재시작
                for label, v in pending.items():
                    try:
                        rows = await sync_history_one(v["mac"], q_last_sensor_ms(db, label))
                        for r in rows:
                            r["sensor"] = label
                        q_store_hist(db, rows, "s")          # authoritative → .db(passive 덮어씀)
                        log(f"[{label}] 이력: {len(rows)}개 .db 저장(authoritative)")
                    except BleakError as e:
                        log(f"[{label}] 이력 연결오류: {e}"); failed[label] = v
                    except Exception as e:
                        log(f"[{label}] 이력 오류: {e}"); failed[label] = v
                    await asyncio.sleep(1)   # 센서 간 간격
            pending = failed                                 # 잠금 해제 → 스캐너 재개
        if pending:
            log(f"[이력] 최종 실패: {list(pending)} — 다음 주기에 재시도")
        q_drain(db)   # .db → 시트 (온라인일 때만)
        await asyncio.sleep(HISTORY_INTERVAL)


async def hourly_flusher(cfg, avg_state, latest, db):
    """매 정시: passive로 모은 시간가중 avg + 시간별 min/max를 .db에 '영구 저장'.
    - avg  → avg_q(→농막_평균),  min/max → hist_q src='p'(→농막_시간별, authoritative가 덮어씀)
    - 진행 중이던 첫 버킷은 부분이라 제외. db=None(모니터/테스트)이면 로그만.
    - 저장 후 온라인이면 드레인(업로드). 크래시·정전에도 직전 완료 시간까지 .db에 안전(유실 ≤1h)."""
    start_ts = time.time()
    last_bucket = int(start_ts // AVG_BUCKET)
    can_store = db is not None
    if not can_store:
        why = "모니터 모드" if MONITOR else f"버킷 {AVG_BUCKET:.0f}s < 1h"
        log(f"[정시] {why} → 로그만(저장/업로드 안 함)")
    else:
        na, nh = q_pending(db, "avg_q"), q_pending(db, "hist_q")
        if na or nh:
            log(f"[큐] 로컬 대기 avg {na} / hist {nh} — 업로드 시도(오프라인 동안 쌓인 분)")
        q_drain(db)   # 시작 시: 지난 실행/오프라인 동안 밀린 것 먼저
    while True:
        await asyncio.sleep(AVG_CHECK)
        b = int(time.time() // AVG_BUCKET)
        if b <= last_bucket:
            continue
        bucket_end = b * AVG_BUCKET
        full = (last_bucket * AVG_BUCKET) >= start_ts   # 버킷 전체를 관측했는가(부분 버킷 제외)
        t_ms = last_bucket * AVG_BUCKET * 1000
        avg_batch, hist_batch, batt_batch, parts = [], [], [], []
        for label in cfg:
            st = avg_state[label]
            at = st["temp"].flush(bucket_end)
            ah = st["humi"].flush(bucket_end)
            tmin, tmax, hmin, hmax = st["tmin"], st["tmax"], st["hmin"], st["hmax"]
            st["tmin"] = st["tmax"] = st["hmin"] = st["hmax"] = None   # 다음 버킷 위해 리셋
            if not full:
                continue
            if at is not None or ah is not None:
                r = {"sensor": label, "time_ms": t_ms}
                if at is not None: r["temp"] = round(at, 1)
                if ah is not None: r["hum"] = round(ah, 1)
                avg_batch.append(r)
            if tmin is not None or hmin is not None:
                # passive 라벨 t_ms = 관측 시작 정시(예: 09:00 = 09~10시).
                # 센서 authoritative 라벨(sync_history_one)과 같은 정시여야 덮어쓰기(교정)가 맞물린다.
                # 농막 현장 첫 sync 때 09:00 등 급변 시간의 min/max를 read-only로 덤프해 offset 확인할 것
                # (offset +1로 밝혀지면 아래 t_ms 에만 +AVG_BUCKET*1000 추가. sync_history_one은 건드리지 말 것
                #  — 농막_시간별/최근5일/비교가 그 라벨 규약에 묶여 있음).
                hist_batch.append({"sensor": label, "time_ms": t_ms,
                                   "maxT": tmax, "minT": tmin, "maxH": hmax, "minH": hmin})
                parts.append(f"{label} {tmin if tmin is not None else '-'}~{tmax if tmax is not None else '-'}℃")
            b_pct = latest.get(label, {}).get("batt")
            if b_pct is not None:   # 배터리 수명 추세용(매시간 1점)
                batt_batch.append({"sensor": label, "time_ms": t_ms,
                                   "volt": round(2.2 + 0.9 * b_pct / 100, 3), "pct": b_pct})
        last_bucket = b
        if avg_batch or hist_batch:
            hhmm = time.strftime("%H:%M", time.localtime(t_ms / 1000))
            log(f"[정시] {hhmm}  avg {len(avg_batch)}센서 · 범위 {'  '.join(parts)}")
        if not can_store:
            continue
        q_store_avg(db, avg_batch)
        q_store_hist(db, hist_batch, "p")   # passive 근사 min/max (authoritative 없을 때만 들어감)
        q_store_batt(db, batt_batch)
        q_drain(db)


async def passive_loop(cfg, latest, avg_state, dirty, box, lock):
    by_mac = {v["mac"].upper(): (label, bytes.fromhex(v["key"])) for label, v in cfg.items()}
    loop = asyncio.get_running_loop()   # 콜백이 다른 스레드(WinRT)일 수 있어 threadsafe 로 깨움
    sig = {label: {} for label in cfg}  # 마지막으로 '변화'로 인정된 값(데드밴드 기준점)
    order = [l for l in ("바닥외부", "내부1층", "다락") if l in cfg]
    order += [l for l in cfg if l not in order]   # 로그 표시 순서(바닥·1층·다락)
    last_cnt = {}                        # 센서별 마지막 프레임 카운터(sd[4]) — 중복 전송 판별
    dstate = {"line": None, "dots": 0}   # 점 누적용(값 그대로인 고유 비콘마다 점 1개)

    def snap(changed, td, hd):
        """온도3 | 습도3 스냅샷(순서 바닥·1층·다락, 바뀐 센서만 괄호에 증분)."""
        temps, humis = [], []
        for L in order:
            v = latest[L]
            t, h = v.get("temp"), v.get("humi")
            ts = f"{t:.1f}" if t is not None else "-"
            hs = f"{round(h)}" if h is not None else "-"
            if L == changed:
                if td is not None: ts += f"({td:+.1f})"
                if hd is not None: hs += f"({hd:+.1f})"
            temps.append(ts); humis.append(hs)
        return f"[수신] {' '.join(temps)} | {' '.join(humis)}"

    def cb(dev, adv):
        mac = (dev.address or "").upper()
        if mac not in by_mac:
            return
        sd = adv.service_data.get(FE95)
        if not sd or len(sd) < 5:
            return
        label, key = by_mac[mac]
        cnt = sd[4]                       # 프레임 카운터(광고 이벤트마다 +1)
        if last_cnt.get(label) == cnt:    # 같은 counter = 일부러 반복 전송 → 무시
            return
        last_cnt[label] = cnt
        pt = decrypt_mibeacon(sd, key)
        if pt is None:
            return
        vals = parse_objects(pt)
        if not vals:
            return
        now = time.time()
        prev = latest[label]
        st = avg_state[label]
        is_change = False
        temp_d = humi_d = None   # 이번 비콘에서 바뀐 값의 증분(바뀐 센서만 표시)
        if "temp" in vals:
            t = vals["temp"]
            st["temp"].update(t, now)                                     # 평균(시간가중)
            st["tmin"] = t if st["tmin"] is None else min(st["tmin"], t)  # passive min/max
            st["tmax"] = t if st["tmax"] is None else max(st["tmax"], t)
            if "temp" not in prev or t != prev["temp"]:
                temp_d = (t - prev["temp"]) if "temp" in prev else 0.0
                is_change = True
        if "humi" in vals:
            h = vals["humi"]
            st["humi"].update(h, now)   # 평균은 모든 샘플 사용(데드밴드 무관)
            st["hmin"] = h if st["hmin"] is None else min(st["hmin"], h)
            st["hmax"] = h if st["hmax"] is None else max(st["hmax"], h)
            base = sig[label].get("humi")
            if base is None or abs(h - base) >= HUMI_DEADBAND:
                humi_d = (h - base) if base is not None else 0.0
                is_change = True
                sig[label]["humi"] = h
        vals["ts"] = now
        prev.update(vals)
        if is_change:
            loop.call_soon_threadsafe(dirty.set)   # 변화 → '농막_현재' 업로더 깨움(쿨다운은 업로더가 관리)
        if BEACON_LOG == "off":
            return
        if BEACON_LOG == "all":            # 모든 고유 비콘을 전체값 한 줄로
            log(snap(label, temp_d, humi_d))
            return
        # 기본(change): 값이 바뀌면 새 줄, 고유 비콘마다 '수신 센서 번호'(바닥0·1층1·다락2) 추가
        mk = str(order.index(label))
        if is_change or dstate["line"] is None:
            dstate["line"] = snap(label, temp_d, humi_d)
            dstate["dots"] = 1
            log_open(f'{dstate["line"]} ')   # 개행 없이 줄 시작
            mark(mk)                          # 이 비콘 = 센서 번호 1개
        else:
            dstate["dots"] += 1
            # 다른 로그([현재]·[이력] 등)에 끊겼거나 60자 넘으면 [수신] 줄을 다시 찍어 맥락 유지
            if (not _mid_dots) or dstate["dots"] % 60 == 0:
                log_open(f'{dstate["line"]} ')
            mark(mk)

    while True:  # 스캐너가 죽거나, 이력 sync로 일시정지되면 재시작
        try:
            async with lock:                 # 이력 sync와 같은 lock으로 스캔/연결 배타
                scanner = BleakScanner(detection_callback=cb)
                await scanner.start()
                box["scanner"] = scanner
            log("[passive] 비콘 수신 시작")
            while box["scanner"] is not None:   # 이력 sync가 None으로 바꾸면 빠져나와 재시작
                await asyncio.sleep(2)
        except Exception as e:
            log(f"[passive] 스캐너 오류, 10초 후 재시작: {e}")
            async with lock:
                sc = box["scanner"]
                if sc is not None:
                    try: await sc.stop()
                    except Exception: pass
                    box["scanner"] = None
            await asyncio.sleep(10)


async def drain_loop(db):
    # 인터넷이 중간에 생겼을 때(방문·테더링) 밀린 .db를 ~1분 내 올리기 위한 주기 드레인.
    while True:
        await asyncio.sleep(60)
        q_drain(db)


async def main():
    # 2차 인스턴스 안전장치: 대상/키/큐는 반드시 '함께' 지정(일부만 주면 농막으로 오업로드 위험).
    _site_env = [os.environ.get("GW_WEBAPP_URL"), os.environ.get("GW_KEYS_FILE"), os.environ.get("GW_QUEUE_DB")]
    if any(_site_env) and not all(_site_env):
        log("[중단] GW_WEBAPP_URL·GW_KEYS_FILE·GW_QUEUE_DB 는 모두 함께 지정해야 합니다(하나라도 빠지면 중단).")
        sys.exit(2)
    if not MONITOR and SECRET in ("", "CHANGE-ME"):   # 모니터는 업로드 안 하므로 SECRET 불필요
        log("[중단] 환경변수 FARMHUT_SECRET 를 설정하세요.")
        sys.exit(2)
    log(f"대상 시트: ...{WEBAPP_URL[-14:]}  키: {os.path.basename(CFG)}  큐: {os.path.basename(QUEUE_DB)}")
    cfg = load_cfg()
    latest = {label: {} for label in cfg}
    avg_state = {label: {"temp": TWAvg(), "humi": TWAvg(),
                         "tmin": None, "tmax": None, "hmin": None, "hmax": None} for label in cfg}
    dirty = asyncio.Event()   # 값 변화 신호(passive → current_uploader)
    scan_lock = asyncio.Lock()        # 스캔(passive)과 연결(이력)의 BlueZ 동시사용 방지
    box = {"scanner": None}           # 현재 passive 스캐너 핸들(이력 sync가 멈출 수 있게 공유)
    db = q_connect() if (not MONITOR and AVG_BUCKET >= 3600) else None   # 1h 미만/모니터는 로컬저장 안 함
    if MONITOR:
        log("모니터 모드: 수신 로그만 — 서버 업로드 없음 · 센서 연결 없음 (평균은 계산·로그만)")
        await asyncio.gather(
            passive_loop(cfg, latest, avg_state, dirty, box, scan_lock),
            hourly_flusher(cfg, avg_state, latest, db),   # db=None → 로그만
        )
    else:
        log(f"게이트웨이 시작 (현재 변화시+{CURRENT_COOLDOWN:.0f}s쿨다운/하트비트 {CURRENT_INTERVAL:.0f}s / 평균 {AVG_BUCKET:.0f}s / 이력 {HISTORY_INTERVAL:.0f}s)")
        tasks = [
            passive_loop(cfg, latest, avg_state, dirty, box, scan_lock),
            current_uploader(cfg, latest, dirty),
            hourly_flusher(cfg, avg_state, latest, db),
        ]
        if db is not None:
            tasks += [history_syncer(cfg, box, scan_lock, db), drain_loop(db)]
        await asyncio.gather(*tasks)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
