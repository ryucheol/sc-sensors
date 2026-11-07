#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""선반 센서 독립 검증(농막 키파일·시트 무관). 사용: python3 verify_shelf.py [초=120] [keys=shelf_keys.json]
   passive_gateway 의 decrypt_mibeacon/parse_objects 를 그대로 빌려 FE95 프레임을 복호화하고
   프레임(카운터)별 객체 종류·값·RSSI, 복제 수, 온도 도착 간격을 출력한다."""
import sys, os, time, json, asyncio, statistics, collections
sys.path.insert(0, os.path.expanduser("~/smart-cabin"))
import passive_gateway as G
from bleak import BleakScanner

SECS = float(sys.argv[1]) if len(sys.argv) > 1 else 120.0
KEYS = sys.argv[2] if len(sys.argv) > 2 else os.path.expanduser("~/smart-cabin/shelf_keys.json")
cfg = json.load(open(KEYS, encoding="utf-8"))
label, v = next(iter(cfg.items()))
MAC = v["mac"].upper(); KEY = bytes.fromhex(v["key"])
FE95 = "0000fe95-0000-1000-8000-00805f9b34fb"

raw = 0; frames = []  # (t, cnt, objs, rssi)
copies = collections.Counter(); last_cnt = None

def cb(dev, adv):
    global raw, last_cnt
    if (dev.address or "").upper() != MAC: return
    sd = adv.service_data.get(FE95)
    if not sd or len(sd) < 5: return
    raw += 1; cnt = sd[4]; copies[cnt] += 1
    if cnt == last_cnt: return
    last_cnt = cnt
    pt = G.decrypt_mibeacon(sd, KEY)
    objs = G.parse_objects(pt) if pt else None
    t = time.time()
    frames.append((t, cnt, objs, adv.rssi))
    print("%s cnt=%3d rssi=%s %s" % (time.strftime("%H:%M:%S"), cnt, adv.rssi,
          objs if objs is not None else "복호화 실패(키 불일치?)"), flush=True)

async def main():
    sc = BleakScanner(detection_callback=cb)
    await sc.start(); print("스캔 %s(%s) %.0f초…" % (label, MAC, SECS), flush=True)
    await asyncio.sleep(SECS); await sc.stop()
    print("\n=== 요약 ===")
    print("raw 패킷 %d, 고유 프레임 %d, 복제/프레임 %s" % (raw, len(frames),
          ("%.1f" % statistics.mean([copies[c] for _, c, _, _ in frames])) if frames else "-"))
    ok = [f for f in frames if f[2]]
    print("복호화 성공 %d / %d" % (len(ok), len(frames)))
    kinds = collections.Counter("+".join(sorted(k for k in f[2] if k in ("temp", "humi", "batt"))) for f in ok)
    print("객체 구성:", dict(kinds))
    tt = [f[0] for f in ok if "temp" in f[2]]
    if len(tt) > 1:
        g = [tt[i+1]-tt[i] for i in range(len(tt)-1)]
        print("온도 객체 도착 간격(s): 중앙 %.1f 평균 %.1f min %.1f max %.1f" % (statistics.median(g), statistics.mean(g), min(g), max(g)))
    tv = [(f[0], f[2]["temp"]) for f in ok if "temp" in f[2]]
    ch = [tv[i+1][0]-tv[i][0] for i in range(len(tv)-1) if tv[i+1][1] != tv[i][1]]
    if ch: print("온도 '값 변화' 간격(s): 중앙 %.1f (n=%d)" % (statistics.median(ch), len(ch)))
    rs = [f[3] for f in frames if f[3] is not None]
    if rs: print("RSSI 평균 %.0f dBm" % statistics.mean(rs))
    b = [f[2]["batt"] for f in ok if "batt" in f[2]]
    if b: print("배터리 %s%%" % b[-1])

asyncio.run(main())
