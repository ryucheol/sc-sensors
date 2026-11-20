#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fill_hourly_gaps.py — 시간별 기록(.db hist_q·avg_q)에 빠진 시간을 thermal_log.csv(1분)로 채운다. cron 매시 30분.

왜: 게이트웨이가 재시작되면 그 시간 버킷은 '부분'이라 버리고, 정시 집계가 어긋나면 한 시간이 통째로 빠진다
    → 시트(농막_시간별·농막_평균)와 예보 탭 그래프가 끊긴다. 히터 제어기는 같은 시간에도 1분마다
    thermal_log.csv에 세 센서 값을 남기므로 그걸로 최고·최저·평균을 다시 만든다.
- 넣기만 한다(INSERT OR IGNORE, src='p', sent=0). 이미 있는 행·센서 원본(src='s')은 건드리지 않는다.
  업로드는 게이트웨이가 다음 정시에 q_drain으로 함께 보낸다(여기선 네트워크 안 씀).
- 한 시간에 그 센서 값이 MIN_N개 미만이거나, 값이 한 번도 안 바뀌었으면(게이트웨이가 멈춰
  latest.json이 굳은 것) 채우지 않는다.
- 대상: 최근 --days일(기본 2) 중 끝난 지 GRACE분 지난 시간(게이트웨이 정시 집계와 겹치지 않게).
실행: python3 gateway/fill_hourly_gaps.py [--days N] [--dry]
"""
import csv, datetime, io, os, sqlite3, sys, time

HERE = os.path.dirname(os.path.realpath(__file__))
BASE = os.path.dirname(HERE)
CSV = os.path.join(BASE, "thermal_log.csv")
DB = os.path.join(BASE, "gateway_queue.db")
COLS = {"내부1층": ("t1", "h1"), "다락": ("t2", "h2"), "바닥외부": ("to", "ho")}   # thermal_log 열 ↔ 센서 이름
MIN_N = 30          # 한 시간에 최소 30분치 값
GRACE = 20 * 60     # 시간이 끝나고 20분 뒤부터(게이트웨이 정시 저장 이후)
HOUR = 3600


def _f(v):
    try: return float(v)
    except (TypeError, ValueError): return None


def aggregate(rows):
    """[(epoch, {열: 값})] → {(시작 epoch, 센서): {maxT,minT,maxH,minH,temp,hum,n}} — 순수."""
    buck = {}
    for ts, r in rows:
        h = int(ts // HOUR) * HOUR
        for s, (tc, hc) in COLS.items():
            t, hm = _f(r.get(tc)), _f(r.get(hc))
            if t is None: continue
            buck.setdefault((h, s), []).append((t, hm))
    out = {}
    for k, v in buck.items():
        if len(v) < MIN_N or len(set(v)) < 2: continue   # 너무 적거나 굳은 값
        T = [a for a, _ in v]; H = [b for _, b in v if b is not None]
        out[k] = {"maxT": max(T), "minT": min(T), "temp": round(sum(T) / len(T), 1), "n": len(v),
                  "maxH": round(max(H)) if H else None, "minH": round(min(H)) if H else None,
                  "hum": round(sum(H) / len(H), 1) if H else None}
    return out


def candidates(agg, have, now):
    """채울 (시작, 센서) 목록 — have에 없고 끝난 지 GRACE 지난 것. 순수."""
    return sorted(k for k in agg if k not in have and k[0] + HOUR + GRACE <= now)


def read_tail(path, since):
    """thermal_log.csv에서 since(epoch) 이후 줄만 — 파일 끝부분만 읽는다."""
    need = int((time.time() - since) / 60 + 120) * 140   # 1분 1줄, 넉넉히 줄당 140바이트
    with open(path, "rb") as f:
        head = f.readline().decode("utf-8").strip().split(",")
        f.seek(0, 2); size = f.tell()
        if size > need: f.seek(size - need); f.readline()   # 잘린 첫 줄 버림
        else: f.seek(0); f.readline()
        text = f.read().decode("utf-8", "replace")
    rows = []
    for r in csv.DictReader(io.StringIO(text), fieldnames=head):
        try: ts = time.mktime(datetime.datetime.strptime(r["ts"], "%Y-%m-%d %H:%M:%S").timetuple())
        except (TypeError, ValueError, KeyError): continue
        if ts >= since: rows.append((ts, r))
    return rows


def main(argv):
    days = float(argv[argv.index("--days") + 1]) if "--days" in argv else 2
    dry = "--dry" in argv
    now = time.time()
    since = (int((now - days * 86400) // HOUR)) * HOUR
    if not os.path.exists(CSV) or not os.path.exists(DB): return 0
    agg = aggregate(read_tail(CSV, since))
    db = sqlite3.connect(DB, timeout=30)
    have = {(ms // 1000, s) for ms, s in db.execute("SELECT time_ms, sensor FROM hist_q WHERE time_ms>=?", (since * 1000,))}
    todo = candidates(agg, have, now)
    for h, s in todo:
        a = agg[(h, s)]
        print("[%s] %s %s 채움 %.1f~%.1f°C 평균 %.1f (n=%d)%s" % (
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), datetime.datetime.fromtimestamp(h).strftime("%m-%d %H:00"),
            s, a["minT"], a["maxT"], a["temp"], a["n"], " (dry)" if dry else ""))
        if dry: continue
        db.execute("INSERT OR IGNORE INTO hist_q(time_ms,sensor,maxT,minT,maxH,minH,src,sent) VALUES(?,?,?,?,?,?,'p',0)",
                   (h * 1000, s, a["maxT"], a["minT"], a["maxH"], a["minH"]))
        db.execute("INSERT OR IGNORE INTO avg_q(time_ms,sensor,temp,hum,sent) VALUES(?,?,?,?,0)",
                   (h * 1000, s, a["temp"], a["hum"]))
    db.commit()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
