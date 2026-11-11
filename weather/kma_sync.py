#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""기상청 단기예보·부여 관측 → 구글 시트 자동 갱신 (1단계). 설계: docs/KMA_SHEET_PUSH.ko.md

실행 경로(모두 같은 잠금 파일을 써서 겹치지 않음):
  - systemd kma-sync.timer (매시 12·42분)  → kma-sync.service
  - NetworkManager 디스패처 90-kma-sync (연결될 때) → kma-sync.service
  - 시트 '명령' 탭의 '기상' → heater/sheet_cmd_poll.py 가 이 스크립트를 --force 로 직접 실행

하는 일:
  0) 시간대가 KST인지, 시계가 NTP로 맞춰졌는지 확인(안 맞으면 중단 — 날짜·발표시각 오판 방지)
  1) 오프라인이면 즉시 종료
  2) 단기예보: 새 발표가 나왔을 때만 어제 23시 발표 + 최신 발표를 받아 '오늘 00시부터 81개 시각' 표 작성
  3) 부여(236) 관측: 시트 마지막 시각(웹앱 ?q=weather) 다음 시각부터 지금 정시까지(최대 31일, 7일씩)
  4) 웹앱 mode:"weather" 로 한 번에 업로드. 실패하면 상태 파일을 갱신하지 않아 다음 실행에서 재시도
  2단계(중기예보·일자료)는 아직 없음.

옵션: --force(발표 비교 없이 예보 재전송) · --dry-run(업로드 대신 페이로드 출력, 상태 저장 안 함)
비밀: 인증키(kma.env)·SECRET(gateway.env)은 로그·출력에 찍지 않는다.
"""
import os, sys, json, time, datetime, subprocess, urllib.request, urllib.parse

HERE = os.path.dirname(os.path.realpath(__file__))
BASE = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.join(BASE, "heater"))

import forecast_fetch as ff          # noqa: E402  auth_key(), online(), fetch(), base_candidates()
import kma_obs_fetch as ko           # noqa: E402  fetch(key, stn, tm1, tm2)

STATE = os.path.join(BASE, "kma_sync_state.json")
LOG = os.path.join(BASE, "kma_sync.log")
LOCK = os.path.join(BASE, ".kma_sync.lock")
N_FC_ROWS = 81                 # 동지집_예보 A3:G83
OBS_MAX_DAYS = 31              # 오래 꺼져 있었어도 이만큼만 백필
OBS_CHUNK_DAYS = 7             # 관측 API 한 번에 요청하는 기간
OBS_DEFAULT_HOURS = 48         # 시트 마지막 시각을 모를 때
FC_ROWS_PARAM = 2000           # 5일치 전 항목(1000이면 잘릴 수 있음)
STN = "236"                    # 부여 ASOS
SKY_TXT = {1: "맑음", 3: "구름많음", 4: "흐림"}
PTY_TXT = {1: "비", 2: "비/눈", 3: "눈", 4: "소나기", 5: "빗방울", 6: "빗방울눈날림", 7: "눈날림"}
IDX_RN, IDX_CA = 15, 25        # kma_sfctm3: RN(1시간 강수) · CA_TOT(전운량)


def log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    try:
        with open(LOG, "a", encoding="utf-8") as f: f.write(line + "\n")
    except Exception: pass


# ── 0) 시간대·시계 ────────────────────────────────────────────────

def tz_is_kst():
    return time.strftime("%z") == "+0900"


def ntp_synced():
    """True/False, timedatectl이 없으면 None(개발 PC 등)."""
    try:
        r = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                           capture_output=True, text=True, timeout=10)
    except Exception:
        return None
    v = r.stdout.strip().lower()
    if r.returncode != 0 or v not in ("yes", "no"): return None
    return v == "yes"


def wait_clock(max_wait=180, step=10):
    """NTP 동기화를 최대 max_wait초 기다린다. 끝내 안 되면 False."""
    t0 = time.time()
    while True:
        s = ntp_synced()
        if s is None or s: return True
        if time.time() - t0 >= max_wait: return False
        time.sleep(step)


# ── 2) 단기예보 ────────────────────────────────────────────────────

def _num(v):
    try:
        f = float(v)
        return int(f) if f == int(f) else f
    except Exception:
        return ""


def sky_text(sky, pty):
    try: pty = int(pty)
    except Exception: pty = 0
    if pty: return PTY_TXT.get(pty, "강수")
    try: return SKY_TXT.get(int(sky), "")
    except Exception: return ""


def merge_items(*item_lists):
    """여러 발표분을 (fcstDate,fcstTime)별로 합침. 뒤에 준 발표가 같은 시각을 통째로 덮는다."""
    out = {}
    for items in item_lists:
        per = {}
        for it in items or []:
            per.setdefault((it["fcstDate"], it["fcstTime"]), {})[it["category"]] = it["fcstValue"]
        out.update(per)
    return out


def vilage_rows(merged, today, n=N_FC_ROWS):
    """today(date) 00시 이후 시각부터 n개: [시각, 기온, 습도, 강수확률, 강수량, 하늘, 풍속]."""
    t0 = today.strftime("%Y%m%d") + "0000"
    rows = []
    for (d, t) in sorted(merged):
        if d + t < t0: continue
        c = merged[(d, t)]
        dt = datetime.datetime.strptime(d + t, "%Y%m%d%H%M")
        rows.append([dt.strftime("%Y-%m-%d %H:%M"), _num(c.get("TMP")), _num(c.get("REH")),
                     _num(c.get("POP")), c.get("PCP", ""), sky_text(c.get("SKY"), c.get("PTY")),
                     _num(c.get("WSD"))])
        if len(rows) >= n: break
    return rows


def expected_base(now):
    """지금 받을 수 있는 최신 발표(발표 15분 뒤부터). 'YYYYMMDDHHMM'."""
    bd, bt = ff.base_candidates(now)[0]
    return bd + bt


def get_vilage(key, now, fetch=ff.fetch):
    """→ (base_prev, base, rows). 어제 23시 발표로 오늘 앞 시간을 채우고 최신 발표로 덮는다."""
    latest, used = None, None
    for bd, bt in ff.base_candidates(now):
        got = fetch(key, bd, bt, FC_ROWS_PARAM)
        if got: latest, used = got, bd + bt; break
    if not latest:
        raise RuntimeError("단기예보 수신 실패(모든 발표 NODATA)")
    prev_base = (now - datetime.timedelta(days=1)).strftime("%Y%m%d") + "2300"
    prev = None
    if used != prev_base:
        try: prev = fetch(key, prev_base[:8], prev_base[8:], FC_ROWS_PARAM)
        except Exception as e: log("어제 23시 발표 수신 실패(최신 발표만 사용): %s" % e)
    rows = vilage_rows(merge_items(prev, latest), now.date())
    return (prev_base if prev else ""), used, rows


# ── 3) 관측 ───────────────────────────────────────────────────────

def _obs_val(tok, i):
    try: v = float(tok[i])
    except Exception: return ""
    return "" if v <= -9 else _num(v)


def obs_row(tm, tok):
    """kma_sfctm3 한 줄 → [시각, 기온, 습도, 강수, 운량]. 강수 결측(-9)=0(무강수)."""
    dt = datetime.datetime.strptime(tm, "%Y%m%d%H%M").strftime("%Y-%m-%d %H:%M")
    rn = _obs_val(tok, IDX_RN)
    return [dt, _obs_val(tok, ko.IDX["ta"]), _obs_val(tok, ko.IDX["hm"]), rn if rn != "" else 0,
            _obs_val(tok, IDX_CA)]


def obs_window(now, last_str):
    """업로드할 관측 구간 (start, end) datetime. last_str='YYYY-MM-DD HH:MM'(시트 마지막) 또는 None."""
    end = now.replace(minute=0, second=0, microsecond=0)
    floor = end - datetime.timedelta(days=OBS_MAX_DAYS)
    if last_str:
        try: start = datetime.datetime.strptime(last_str, "%Y-%m-%d %H:%M") + datetime.timedelta(hours=1)
        except ValueError: start = end - datetime.timedelta(hours=OBS_DEFAULT_HOURS)
    else:
        start = end - datetime.timedelta(hours=OBS_DEFAULT_HOURS)
    return max(start, floor), end


def get_obs(key, start, end, fetch=ko.fetch):
    rows = []
    t = start
    while t <= end:
        t2 = min(end, t + datetime.timedelta(days=OBS_CHUNK_DAYS) - datetime.timedelta(hours=1))
        got = fetch(key, STN, t.strftime("%Y%m%d%H00"), t2.strftime("%Y%m%d%H00"))
        rows += [obs_row(tm, got[tm]) for tm in sorted(got)]
        t = t2 + datetime.timedelta(hours=1)
    return rows


# ── 4) 업로드 ─────────────────────────────────────────────────────

def webapp():
    import sheet_cmd_poll as scp            # gateway.env: GW_WEBAPP_URL(없으면 게이트웨이 기본)·FARMHUT_SECRET
    url, secret = scp.load_env()
    return url, secret, scp.post


def weather_last(url):
    """웹앱 GET ?q=weather → {'obs_last': 'YYYY-MM-DD HH:MM', ...}. v11 이전이면 {}."""
    try:
        with urllib.request.urlopen(url + "?q=weather", timeout=30) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        return d.get("weather_last") or {}
    except Exception as e:
        log("시트 마지막 시각 조회 실패(상태 파일로 대체): %s" % e)
        return {}


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f: return json.load(f)
    except Exception: return {}


def save_state(st):
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f: json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE)


def run(force=False, dry=False, now=None):
    """한 번 동기화. 반환 코드: 0 정상/할 일 없음, 1 실패."""
    if not tz_is_kst():
        log("중단: 시간대가 KST가 아님(%s) — sudo timedatectl set-timezone Asia/Seoul" % time.strftime("%Z %z"))
        return 1
    if not ff.online():
        log("오프라인 — 건너뜀"); return 0
    if os.environ.get("KMA_SYNC_SKIP_CLOCK") != "1" and not wait_clock():
        log("중단: 시계가 NTP로 맞춰지지 않음(3분 대기) — timedatectl 확인"); return 1
    now = now or datetime.datetime.now()
    key = ff.auth_key()
    if not key:
        log("중단: 기상청 인증키 없음(~/smart-cabin/kma.env)"); return 1
    url, secret, post = webapp()
    if not url:
        log("중단: 웹앱 URL 없음(gateway.env GW_WEBAPP_URL)"); return 1

    st = load_state()
    srv = weather_last(url)
    payload = {"secret": secret, "mode": "weather", "fetched_at": now.strftime("%Y-%m-%d %H:%M")}
    did = []

    want = expected_base(now)
    if force or st.get("vilage_base") != want or st.get("vilage_day") != now.strftime("%Y-%m-%d"):
        try:
            prev_b, base, rows = get_vilage(key, now)
            payload["vilage"] = {"base_prev": prev_b, "base": base, "rows": rows}
            did.append("예보 %s %d행" % (base, len(rows)))
        except Exception as e:
            log("단기예보 실패: %s" % e)

    start, end = obs_window(now, srv.get("obs_last") or st.get("obs_last"))
    if start <= end:
        try:
            rows = get_obs(key, start, end)
            if rows:
                payload["obs_hourly"] = rows
                did.append("관측 %s~%s %d행" % (rows[0][0], rows[-1][0], len(rows)))
        except Exception as e:
            log("관측 실패: %s" % e)

    if "vilage" not in payload and "obs_hourly" not in payload:
        log("할 일 없음"); return 0
    if dry:
        shown = dict(payload, secret="***")
        print(json.dumps(shown, ensure_ascii=False, indent=1)); return 0

    try:
        res = post(url, payload)
    except Exception as e:
        log("업로드 실패: %s — 다음 실행에서 재시도" % e); return 1
    if not res.get("ok"):
        log("업로드 오류: %s — 웹앱이 v11인지, SECRET이 맞는지 확인" % res.get("error")); return 1
    # v10b 이하 웹앱은 mode:weather 를 몰라 {"ok":true,"note":"empty"} 로 답한다 → 기록 안 됐는데 성공으로 착각 방지
    if ("vilage" in payload and "vilage" not in res) or ("obs_hourly" in payload and "obs_hourly" not in res):
        log("업로드 무시됨: 웹앱이 v11이 아님(응답 %s) — Code.gs v11 배포 후 자동 재시도" % res.get("version", res)); return 1
    if "vilage" in payload:
        st["vilage_base"] = payload["vilage"]["base"]; st["vilage_day"] = now.strftime("%Y-%m-%d")
    if "obs_hourly" in payload:
        st["obs_last"] = (res.get("obs_hourly") or {}).get("last") or payload["obs_hourly"][-1][0]
    st["last_ok"] = now.strftime("%Y-%m-%d %H:%M")
    save_state(st)
    log("완료: %s → %s" % (", ".join(did), {k: v for k, v in res.items() if k in ("vilage", "obs_hourly")}))
    return 0


def main(argv):
    import fcntl
    lk = open(LOCK, "w")
    try: fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)   # 타이머·디스패처·명령 탭 동시 실행 방지
    except OSError:
        print("다른 kma_sync 실행 중 — 종료"); return 0
    return run(force="--force" in argv, dry="--dry-run" in argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
