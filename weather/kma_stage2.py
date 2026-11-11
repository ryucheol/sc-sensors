#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kma_sync 2단계 수집기: 중기예보 · 부여 일자료(확정+오늘 잠정) · 예보검증. 설계: docs/KMA_SHEET_PUSH.ko.md §8

⚠ 중기예보(getMidLandFcst·getMidTa)와 일자료(kma_sfcdd3)는 현장에서 아직 검증 전이다.
   응답 형식이 예상과 다르면 예외로 그 항목만 빠지고(kma_sync가 로그), 1단계는 영향 없음.
   켜기: ~/smart-cabin/kma_stage2.on 파일을 만들거나(touch) KMA_SYNC_STAGE2=1, 또는 kma_sync.py --stage2.
   먼저 `python3 weather/kma_sync.py --stage2 --dry-run` 으로 행 내용을 확인할 것. 웹앱은 Code.gs v12 필요.
"""
import datetime, json, urllib.request, urllib.parse

MID_API = "https://apihub.kma.go.kr/api/typ02/openApi/MidFcstInfoService"
DAY_API = "https://apihub.kma.go.kr/api/typ01/url/kma_sfcdd3.php"
REG_LAND = "11C20000"      # 대전·세종·충남 육상
REG_TA = "11C20501"        # 부여 기온(추정, 미확인 — 시트 A1 문구와 같음)
STN = "236"
MID_DAYS = range(4, 11)    # 4~10일째 → 중기예보 A3:H9 7행
DAILY_MAX_DAYS = 31


# ── 중기예보 ──────────────────────────────────────────────────────

def mid_candidates(now):
    """받을 수 있는 중기 발표(06·18시, 발표 30분 뒤부터) 최신순 'YYYYMMDDHHMM' 3개."""
    out = []
    d = now.date()
    for day in (d, d - datetime.timedelta(days=1)):
        for h in (18, 6):
            t = datetime.datetime(day.year, day.month, day.day, h)
            if now >= t + datetime.timedelta(minutes=30):
                out.append(t.strftime("%Y%m%d%H%M"))
    return out[:3]


def _get_json(url, params):
    with urllib.request.urlopen(url + "?" + urllib.parse.urlencode(params), timeout=30) as r:
        raw = r.read().decode("utf-8", "replace")
    try:
        data = json.loads(raw)
    except Exception:
        raise RuntimeError("비JSON 응답(키 오류 가능): " + raw[:120])
    hdr = data.get("response", {}).get("header", {})
    code = hdr.get("resultCode")
    if code == "03": return None                       # NODATA
    if code not in ("00", None): raise RuntimeError("API 오류 %s %s" % (code, hdr.get("resultMsg", "")))
    items = data["response"]["body"]["items"]["item"]
    return items[0] if isinstance(items, list) else items


def fetch_mid(key, tmfc):
    p = {"authKey": key, "pageNo": "1", "numOfRows": "10", "dataType": "JSON", "tmFc": tmfc}
    land = _get_json(MID_API + "/getMidLandFcst", dict(p, regId=REG_LAND))
    if land is None: return None
    ta = _get_json(MID_API + "/getMidTa", dict(p, regId=REG_TA))
    if ta is None: return None
    return land, ta


def _v(d, k):
    v = d.get(k)
    if v in (None, ""): return ""
    try:
        f = float(v); return int(f) if f == int(f) else f
    except Exception:
        return v


def mid_rows(land, ta, tmfc):
    """→ [[날짜, 오전하늘, 오후하늘, 오전강수%, 오후강수%, 최저, 최고, 비고] × 7]. 필드가 없으면 RuntimeError."""
    base = datetime.datetime.strptime(tmfc[:8], "%Y%m%d").date()
    rows = []
    for n in MID_DAYS:
        if ("wf%dAm" % n) in land and ("wf%dPm" % n) in land:
            am, pm = land["wf%dAm" % n], land["wf%dPm" % n]
            pam, ppm = _v(land, "rnSt%dAm" % n), _v(land, "rnSt%dPm" % n)
            note = "%d일째" % n
        elif ("wf%d" % n) in land:
            am = pm = land["wf%d" % n]
            pam = ppm = _v(land, "rnSt%d" % n)
            note = "%d일째(하루 단위)" % n
        else:
            raise RuntimeError("중기 육상 응답에 wf%d 필드 없음 — 필드: %s" % (n, sorted(land)[:12]))
        rows.append([(base + datetime.timedelta(days=n)).strftime("%Y-%m-%d"), am, pm, pam, ppm,
                     _v(ta, "taMin%d" % n), _v(ta, "taMax%d" % n), note])
    return rows


def get_mid(key, now, fetch=fetch_mid):
    for tm in mid_candidates(now):
        got = fetch(key, tm)
        if got: return tm, mid_rows(got[0], got[1], tm)
    raise RuntimeError("중기예보 수신 실패(모든 발표 NODATA)")


# ── 일자료 ────────────────────────────────────────────────────────
# kma_sfcdd3 열 이름(헤더 주석 줄에서 찾음). 이름이 다르면 RuntimeError로 그 항목만 건너뛴다.
DAILY_NEED = ["TA_MIN", "TA_MIN_TM", "TA_MAX", "TA_MAX_TM", "TA_AVG", "TG_MIN", "HM_AVG", "HM_MIN", "RN_DAY", "CA_TOT"]


def parse_sfcdd(txt):
    """→ (header_names, {YYYYMMDD: {name: token}}). 헤더는 TA_MAX가 든 '#' 줄."""
    names = None
    rows = {}
    for line in txt.splitlines():
        if line.startswith("#"):
            t = line.lstrip("#").split()
            if "TA_MAX" in t and "STN" in t and names is None: names = t
            continue
        t = line.split()
        if not t or not t[0][:8].isdigit() or names is None: continue
        if len(t) < len(names): continue
        rows[t[0][:8]] = dict(zip(names, t))
    if names is None: raise RuntimeError("일자료 헤더(TA_MAX 포함 줄)를 찾지 못함 — 원문 앞부분: " + txt[:200])
    miss = [n for n in DAILY_NEED if n not in names]
    if miss: raise RuntimeError("일자료 열 이름 없음 %s — 헤더: %s" % (miss, " ".join(names)))
    return names, rows


def fetch_daily(key, d1, d2):
    q = {"tm1": d1.strftime("%Y%m%d"), "tm2": d2.strftime("%Y%m%d"), "stn": STN, "help": "1", "authKey": key}
    with urllib.request.urlopen(DAY_API + "?" + urllib.parse.urlencode(q), timeout=30) as r:
        return r.read().decode("euc-kr", "replace")


def _f(tok):
    try: v = float(tok)
    except Exception: return ""
    if v <= -9: return ""
    return int(v) if v == int(v) else v


def _hhmm(tok):
    s = str(tok).strip()
    if not s.isdigit(): return ""
    s = s.zfill(4)[-4:]
    return s[:2] + ":" + s[2:]


def daily_row(ymd, rec, hm_max=""):
    """일자료 한 줄 → 부여_일별 13열(비고 빈칸 = 확정)."""
    rn = _f(rec["RN_DAY"])
    return [datetime.datetime.strptime(ymd, "%Y%m%d").strftime("%Y-%m-%d"),
            _f(rec["TA_MIN"]), _hhmm(rec["TA_MIN_TM"]), _f(rec["TA_MAX"]), _hhmm(rec["TA_MAX_TM"]),
            _f(rec["TA_AVG"]), _f(rec["TG_MIN"]), _f(rec["HM_AVG"]), _f(rec["HM_MIN"]),
            rn if rn != "" else 0, _f(rec["CA_TOT"]), hm_max, ""]


def provisional_row(day, hourly):
    """오늘 시간자료([시각,기온,습도,강수,운량]) → 잠정 13열. 시각은 정시 해상도, 초상최저 없음."""
    key = day.strftime("%Y-%m-%d")
    hs = [r for r in hourly if r[0].startswith(key) and r[1] != ""]
    if not hs: return None
    lo = min(hs, key=lambda r: r[1]); hi = max(hs, key=lambda r: r[1])
    hm = [r[2] for r in hs if r[2] != ""]
    ca = [r[4] for r in hs if r[4] != ""]
    rn = sum(r[3] for r in hs if isinstance(r[3], (int, float)))
    avg = lambda xs: round(sum(xs) / len(xs), 1) if xs else ""
    return [key, lo[1], lo[0][11:16], hi[1], hi[0][11:16], avg([r[1] for r in hs]), "",
            avg(hm), min(hm) if hm else "", round(rn, 1), avg(ca), max(hm) if hm else "",
            "잠정(%s시까지 시간자료)" % hs[-1][0][11:13]]


def daily_window(today, last_str):
    """확정 일자료를 받을 날짜 범위 (d1, d2). last_str='YYYY-MM-DD'(시트 마지막 확정) 또는 None."""
    d2 = today - datetime.timedelta(days=1)
    if last_str:
        try: d1 = datetime.datetime.strptime(last_str, "%Y-%m-%d").date() + datetime.timedelta(days=1)
        except ValueError: d1 = d2
    else:
        d1 = d2
    return max(d1, today - datetime.timedelta(days=DAILY_MAX_DAYS)), d2


def get_daily(key, today, last_str, hourly_by_day=None, fetch=fetch_daily):
    """→ 확정 행 목록(날짜순). hourly_by_day: {'YYYY-MM-DD': 최고습도} (있으면 L열)."""
    d1, d2 = daily_window(today, last_str)
    if d1 > d2: return []
    _, recs = parse_sfcdd(fetch(key, d1, d2))
    hb = hourly_by_day or {}
    out = []
    for ymd in sorted(recs):
        day = datetime.datetime.strptime(ymd, "%Y%m%d").strftime("%Y-%m-%d")
        out.append(daily_row(ymd, recs[ymd], hb.get(day, "")))
    return out


# ── 예보검증 ──────────────────────────────────────────────────────

def verify_rows(items_23, day):
    """전날 23시 발표 item → day의 0~23시 [시각, 예보기온, 예보습도]. 24행이 안 되면 None."""
    want = day.strftime("%Y%m%d")
    per = {}
    for it in items_23 or []:
        if it["fcstDate"] != want: continue
        per.setdefault(it["fcstTime"], {})[it["category"]] = it["fcstValue"]
    rows = []
    for h in range(24):
        c = per.get("%02d00" % h)
        if not c or "TMP" not in c: return None
        rows.append(["%s %02d:00" % (day.strftime("%Y-%m-%d"), h), _v(c, "TMP"), _v(c, "REH")])
    return rows
