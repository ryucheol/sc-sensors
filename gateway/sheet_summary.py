#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pi 전용 기록을 시트·드라이브로 보낸다 (Code.gs v13 mode:summary · mode:rawfile). 표준 라이브러리만.

1) 시간 요약 → 시트 '농막_시간요약'
   thermal_log.csv(1분 간격: ts,mode,relay,watt,volt,t1,h1,t2,h2,to,ho,t_ctl,delta,fan)를 정시 단위로 묶어
   (aux_log.csv 가 있으면 보조 히터 kWh·가동 분을 끝 두 열에 붙여)
   히터 kWh(실측 W × 실제 간격, 간격은 최대 5분으로 자름)·가동 분·가동 중 평균 W·평균 온습도·다락−1층·선풍기 분·기록 분.
   끝난 시간만 보낸다(지금 시간은 다음 실행에서). 보낸 마지막 시각을 상태 파일에 남겨 이어서 보내고,
   인터넷이 없으면 상태를 안 바꿔 다음 실행에서 재시도(store-and-forward). 시트는 시각 키로 덮어쓰므로 중복 안전.
2) 원자료 → 드라이브 '농막_원자료' 폴더
   하루가 끝나면 그날 1분 기록을 gzip CSV(thermal_log_YYYY-MM-DD.csv.gz, 머리글 포함)로 보낸다. 밀린 날은 모두.

실행: cron 매시 5분  `5 * * * * cd ~/smart-cabin && python3 gateway/sheet_summary.py >> sheet_summary.log 2>&1`
옵션: --dry-run(전송·상태 저장 없이 요약만 출력)  --since YYYY-MM-DD(그날부터 다시 보냄)  --no-raw
설정: gateway.env 의 GW_WEBAPP_URL(없으면 게이트웨이 기본 URL)·FARMHUT_SECRET (heater/sheet_cmd_poll.py 와 같은 방식)
"""
import base64, csv, datetime as dt, gzip, io, json, os, sys

HERE = os.path.dirname(os.path.realpath(__file__))
BASE = os.path.dirname(HERE)
LOG_CSV = os.path.join(BASE, "thermal_log.csv")
STATE = os.path.join(BASE, "sheet_summary_state.json")
MAX_GAP_S = 300          # 기록 간격이 이보다 길면(Pi 정지 등) 그 구간 에너지는 5분까지만 셈
BATCH = 150              # 한 번에 보낼 시간 행 수(Apps Script 시간 제한 고려)
MAX_RAW_DAYS = 10        # 한 번 실행에 보낼 원자료 일수


def parse_ts(s):
    return dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def read_log(path=LOG_CSV):
    rows = []
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            try:
                r["_t"] = parse_ts(r["ts"])
            except (KeyError, ValueError):
                continue
            rows.append(r)
    rows.sort(key=lambda r: r["_t"])
    return rows


AUX_CSV = os.path.join(BASE, "aux_log.csv")


def aux_hourly(rows, start=None, end=None):
    """보조 히터 1분 기록(ts,on,watt) → {정시: (kWh, 가동 분)}. 기록이 없는 시간은 키가 없다(=모름, 0과 구분)."""
    out = {}
    for i, r in enumerate(rows):
        t = r["_t"]; hr = t.replace(minute=0, second=0, microsecond=0)
        if (start and hr < start) or (end and hr >= end):
            continue
        nxt = rows[i + 1]["_t"] if i + 1 < len(rows) else None
        dur = max(min((nxt - t).total_seconds(), MAX_GAP_S) if nxt else 60.0, 0.0)
        w = fnum(r.get("watt")) or 0.0
        on = (r.get("on") or "0").strip() in ("1", "True", "true")
        kwh, mins = out.get(hr, (0.0, 0.0))
        out[hr] = (kwh + (w * dur / 3600.0 if on else 0.0) / 1000.0, mins + (dur / 60.0 if on else 0.0))
    return {k: (round(v[0], 3), round(min(v[1], 60.0), 1)) for k, v in out.items()}


def add_aux(summ, aux):
    """요약 행 끝에 보조 kWh·가동 분 두 칸을 붙인다(기존 열 위치는 그대로 — 기계 탭 열 순서 유지)."""
    for r in summ:
        hr = dt.datetime.strptime(r[0], "%Y-%m-%d %H:00")
        k = aux.get(hr)
        r.extend([k[0], k[1]] if k else ["", ""])
    return summ


def hourly(rows, start=None, end=None):
    """1분 기록 → 정시별 요약 행 목록. start ≤ 시각 < end 인 '끝난 시간'만."""
    out, cur, acc = [], None, None

    def flush():
        if cur is None or acc["n"] == 0:
            return
        def avg(k):
            v = acc[k]
            return round(sum(v) / len(v), 2) if v else ""
        mode = max(acc["modes"].items(), key=lambda kv: kv[1])[0] if acc["modes"] else ""
        on_min = min(acc["on_s"] / 60.0, 60.0)   # 정시를 걸친 마지막 샘플 때문에 60분을 살짝 넘는 것 방지(전력량은 그대로)
        avg_w = round(acc["wh_on"] / (acc["on_s"] / 3600.0)) if acc["on_s"] > 0 else ""
        out.append([cur.strftime("%Y-%m-%d %H:00"), mode, round(acc["wh"] / 1000.0, 3), round(on_min, 1), avg_w,
                    avg("t1"), avg("t2"), avg("to"), avg("h1"), avg("h2"), avg("ho"), avg("delta"),
                    round(min(acc["fan_s"] / 60.0, 60.0), 1), acc["n"]])

    for i, r in enumerate(rows):
        t = r["_t"]
        hr = t.replace(minute=0, second=0, microsecond=0)
        if start and hr < start:
            continue
        if end and hr >= end:
            break
        if hr != cur:
            flush()
            cur = hr
            acc = {"n": 0, "wh": 0.0, "wh_on": 0.0, "on_s": 0.0, "fan_s": 0.0, "modes": {},
                   "t1": [], "t2": [], "to": [], "h1": [], "h2": [], "ho": [], "delta": []}
        # 이 샘플이 대표하는 시간 = 다음 샘플까지(최대 MAX_GAP_S), 마지막이면 60초
        nxt = rows[i + 1]["_t"] if i + 1 < len(rows) else None
        dur = min((nxt - t).total_seconds(), MAX_GAP_S) if nxt else 60.0
        dur = max(dur, 0.0)
        w = fnum(r.get("watt")) or 0.0
        on = (r.get("relay") or "0").strip() in ("1", "True", "true")
        acc["n"] += 1
        acc["wh"] += w * dur / 3600.0
        if on:
            acc["on_s"] += dur
            acc["wh_on"] += w * dur / 3600.0
        if (r.get("fan") or "0").strip() in ("1", "True", "true"):
            acc["fan_s"] += dur
        m = (r.get("mode") or "").strip()
        if m:
            acc["modes"][m] = acc["modes"].get(m, 0) + 1
        for k in ("t1", "t2", "to", "h1", "h2", "ho", "delta"):
            v = fnum(r.get(k))
            if v is not None:
                acc[k].append(v)
    flush()
    return out


def day_csv_gz(rows, day, header):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    for r in rows:
        if r["_t"].date() == day:
            w.writerow([r.get(h, "") for h in header])
    return gzip.compress(buf.getvalue().encode("utf-8"))


def load_state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(st):
    tmp = STATE + ".tmp"
    json.dump(st, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
    os.replace(tmp, STATE)


def main(argv):
    dry = "--dry-run" in argv
    st = load_state()
    rows = read_log()
    if not rows:
        print("기록 없음"); return 0
    now_hr = dt.datetime.now().replace(minute=0, second=0, microsecond=0)
    if "--since" in argv:
        start = dt.datetime.strptime(argv[argv.index("--since") + 1], "%Y-%m-%d")
    elif st.get("last_hour"):
        start = dt.datetime.strptime(st["last_hour"], "%Y-%m-%d %H:%M") + dt.timedelta(hours=1)
    else:
        start = rows[0]["_t"].replace(minute=0, second=0, microsecond=0)
    summ = hourly(rows, start, now_hr)
    if os.path.exists(AUX_CSV):
        add_aux(summ, aux_hourly(read_log(AUX_CSV), start, now_hr))
    print("시간 요약 %d행 (%s ~ %s)" % (len(summ), summ[0][0] if summ else "-", summ[-1][0] if summ else "-"))
    if dry:
        for r in summ[-5:]:
            print("  ", r)
    else:
        sys.path.insert(0, os.path.join(BASE, "heater"))
        import sheet_cmd_poll as scp   # load_env·post 재사용(gateway.env의 URL·SECRET)
        url, secret = scp.load_env()
        for k in range(0, len(summ), BATCH):
            part = summ[k:k + BATCH]
            res = scp.post(url, {"secret": secret, "mode": "summary", "rows": part})
            if not res.get("ok"):
                print("요약 전송 실패:", res); return 1
            st["last_hour"] = part[-1][0][:16]
            save_state(st)
            print("  보냄 %s ~ %s → %s" % (part[0][0], part[-1][0], {k2: res.get(k2) for k2 in ("added", "updated")}))
    if "--no-raw" in argv:
        return 0
    # 원자료: 끝난 날 중 아직 안 보낸 날
    header = [h for h in rows[0].keys() if not h.startswith("_")]
    sent = set(st.get("raw_days", []))
    days = sorted({r["_t"].date() for r in rows if r["_t"].date() < now_hr.date()} - {dt.date.fromisoformat(d) for d in sent})
    for day in days[:MAX_RAW_DAYS]:
        gz = day_csv_gz(rows, day, header)
        name = "thermal_log_%s.csv.gz" % day.isoformat()
        if dry:
            print("  원자료 %s %d바이트(보내지 않음)" % (name, len(gz))); continue
        res = scp.post(url, {"secret": secret, "mode": "rawfile", "name": name, "mime": "application/gzip",
                             "b64": base64.b64encode(gz).decode()})
        if not res.get("ok"):
            print("원자료 전송 실패:", name, res); return 1
        sent.add(day.isoformat())
        st["raw_days"] = sorted(sent)[-60:]
        save_state(st)
        print("  원자료 %s %d바이트 → 드라이브" % (name, len(gz)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
