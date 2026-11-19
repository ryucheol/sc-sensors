#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""문자(SMS) 명령 통로 — LTE 동글(E3372 HiLink)의 문자함으로 명령을 받고 문자로 답한다. 텔레그램의 예비 통로.

왜: LTE 데이터가 끊기거나 다 써도 문자는 통하는 경우가 많다. 그때도 `동파`·`상태` 같은 명령을 보낼 수 있고,
    인터넷이 끊긴 동안 동파 위험이 생기면 Pi가 문자로 알린다. (시험 기록: docs/LTE_DONGLE.ko.md §11)

동작(cron 매분):
  1) 안 읽은 문자를 읽음 처리한다.
  2) 허용 번호(SMS_ALLOWED)에서 온 문자 중 비밀 단어(SMS_PREFIX)로 시작하는 것만 명령으로 실행한다.
     명령은 텔레그램 봇과 같다(telegram_bot.normalize/execute → heater/sheet_cmd_poll.run). 예: `동지 상태`, `동지 동파`.
     비밀 단어만 보내면 `상태`. 60분 넘게 늦게 처리되는 제어 명령은 실행하지 않는다(텔레그램과 같은 규칙).
  3) 결과를 단문(40자, 넘으면 통신사가 장문 MMS로 과금)으로 줄여 답하고, 전체 결과는 텔레그램에도 남긴다(끊겼으면 대기열).
     처리한 명령 문자는 지운다. 그 밖의 문자(통신사 안내 등)는 읽음 처리만 하고 남긴다.
  4) 경보: 텔레그램 대기열이 SMS_ALARM_AFTER_MIN분 넘게 못 나가고 있을 때(=인터넷 끊김) cabin_watch의 중요 경보가
     켜져 있으면 문자로 보낸다. 같은 경보는 SMS_ALARM_REPEAT_H시간에 한 번.
  5) 비용 보호: 이번 달 보낸 문자 조각 수를 세어 SMS_MONTH_MAX(기본 80, 요금제 무료 100건 안)에서 멈춘다.

설정: ~/smart-cabin/sms.env (gitignore, 예시 sms.env.example)
  SMS_ALLOWED=01012345678[,010...]   허용 번호
  SMS_PREFIX=동지                     비밀 단어(명령 앞에 붙임)
실행: python3 notify/sms_bot.py            # 한 번 처리(cron)
      python3 notify/sms_bot.py status     # 설정·이번 달 발송 수·문자함 요약(본문·번호는 가림)
      python3 notify/sms_bot.py test-send  # 허용 번호 첫째에게 시험 문자 1통
"""
import os, sys, re, json, time, fcntl, math, urllib.request
from xml.sax.saxutils import escape, unescape

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.join(REPO, "heater"))

BASE = os.path.expanduser(os.environ.get("SMART_CABIN_DIR", "~/smart-cabin"))
ENV_FILE = os.path.join(BASE, "sms.env")
STATE = os.path.join(BASE, "sms_state.json")
LOG = os.path.join(BASE, "sms_bot.log")
LOCK = os.path.join(BASE, ".sms_bot.lock")
HOST = "http://192.168.8.1"

DEFAULTS = {"SMS_ALLOWED": "", "SMS_PREFIX": "", "SMS_MONTH_MAX": "80", "SMS_REPLY_MAX": "40",
            "SMS_ALARM": "1", "SMS_ALARM_AFTER_MIN": "20", "SMS_ALARM_REPEAT_H": "6", "SMS_TTL_MIN": "60"}
ALWAYS_OK = ("상태", "현황", "센서", "경보", "도움")
ALARM_KEYS = ("low_indoor", "heater_fault", "frost_stuck", "plug_comm", "controller_down", "overheat", "forecast_frost")


def log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f: f.write(line + "\n")
    except Exception:
        pass


def config(path=None):
    c = dict(DEFAULTS)
    try:
        for ln in open(path or ENV_FILE, encoding="utf-8"):
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1); c[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    for k in DEFAULTS:
        if os.environ.get(k): c[k] = os.environ[k]
    c["allowed"] = {norm_phone(x) for x in c["SMS_ALLOWED"].split(",") if norm_phone(x)}
    for k in ("SMS_MONTH_MAX", "SMS_REPLY_MAX", "SMS_ALARM_AFTER_MIN", "SMS_ALARM_REPEAT_H", "SMS_TTL_MIN"):
        c[k] = int(float(c[k]))
    c["SMS_ALARM"] = c["SMS_ALARM"] not in ("0", "false", "no", "")
    return c


# ── 순수 함수 ─────────────────────────────────────────────────────
def norm_phone(s):
    """'+82 10-1234-5678' → '01012345678'. 숫자 없으면 ''."""
    d = re.sub(r"\D", "", s or "")
    if d.startswith("82") and len(d) >= 11: d = "0" + d[2:]
    return d


def mask_phone(s):
    d = norm_phone(s)
    return (d[:3] + "****" + d[-2:]) if len(d) >= 7 else (d or "(없음)")


def parse_command(content, prefix):
    """비밀 단어로 시작하면 그 뒤 명령(빈칸이면 '상태'), 아니면 None."""
    t = (content or "").strip()
    if not prefix or not t.lower().startswith(prefix.lower()): return None
    rest = t[len(prefix):]
    if rest and not (rest[0].isspace() or rest[0] in ",.:;"): return None   # '동지집' 같은 다른 낱말은 명령 아님
    return rest.strip(" \t\r\n,.:;") or "상태"


_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍]")


def compact(text, n):
    """문자용으로 줄이기: 이모지 제거, 줄바꿈 → ' / ', 공백 정리, n글자에서 자르기."""
    t = _EMOJI.sub("", text or "")
    t = " / ".join(x.strip() for x in t.splitlines() if x.strip())
    t = re.sub(r"[ \t]+", " ", t).strip()
    return t if len(t) <= n else t[:n - 1] + "…"


def segments(text):
    """요금 건수 추정: 국내 단문은 80바이트. 동글은 한글이 섞이면 글자당 2바이트(UCS2)라 40자, 영숫자만이면 80자.
    넘으면 통신사가 장문(LMS/MMS)으로 바꿔 전달한다(11-18 휴대폰에 'MMS' 표시) → 넘지 않게 compact로 자른다."""
    lim = 80 if all(ord(ch) < 128 for ch in text) else 40
    return max(1, math.ceil(len(text) / lim))


def month_key(now):
    return time.strftime("%Y-%m", time.localtime(now))


def can_send(st, now, n_seg, month_max):
    m = month_key(now)
    used = st.get("sent", {}).get(m, 0)
    return used + n_seg <= month_max


def parse_date(s):
    try: return time.mktime(time.strptime(s, "%Y-%m-%d %H:%M:%S"))
    except Exception: return None


# ── 동글 HiLink API ───────────────────────────────────────────────
class Hilink:
    def __init__(self, host=HOST, timeout=8, urlopen=None):
        self.host, self.timeout = host, timeout
        self.urlopen = urlopen or urllib.request.urlopen

    def _hdr(self):
        t = self.urlopen(self.host + "/api/webserver/SesTokInfo", timeout=self.timeout).read().decode()
        return {"Cookie": re.search("<SesInfo>(.*?)</SesInfo>", t).group(1),
                "__RequestVerificationToken": re.search("<TokInfo>(.*?)</TokInfo>", t).group(1),
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}

    def call(self, path, body=None):
        req = urllib.request.Request(self.host + path, data=body.encode("utf-8") if body else None, headers=self._hdr())
        x = self.urlopen(req, timeout=self.timeout).read().decode("utf-8", "replace")
        if "<error>" in x:
            raise RuntimeError("%s 오류 %s" % (path, (re.search(r"<code>(\d+)</code>", x) or [None, "?"])[1]))
        return x

    def inbox(self, count=50):
        body = ("<?xml version='1.0' encoding='UTF-8'?><request><PageIndex>1</PageIndex><ReadCount>%d</ReadCount>"
                "<BoxType>1</BoxType><SortType>0</SortType><Ascending>0</Ascending><UnreadPreferred>1</UnreadPreferred>"
                "</request>") % count
        out = []
        for m in re.findall(r"<Message>(.*?)</Message>", self.call("/api/sms/sms-list", body), re.S):
            g = lambda k: unescape((re.search("<%s>(.*?)</%s>" % (k, k), m, re.S) or [None, ""])[1])
            out.append({"index": g("Index"), "unread": g("Smstat") == "0", "phone": g("Phone"),
                        "content": g("Content"), "date": g("Date")})
        return out

    def set_read(self, idx):
        self.call("/api/sms/set-read", "<?xml version='1.0' encoding='UTF-8'?><request><Index>%s</Index></request>" % idx)

    def delete(self, idx):
        self.call("/api/sms/delete-sms", "<?xml version='1.0' encoding='UTF-8'?><request><Index>%s</Index></request>" % idx)

    def send(self, phone, text):
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        body = ("<?xml version='1.0' encoding='UTF-8'?><request><Index>-1</Index><Phones><Phone>%s</Phone></Phones>"
                "<Sca></Sca><Content>%s</Content><Length>%d</Length><Reserved>1</Reserved><Date>%s</Date></request>"
                ) % (escape(phone), escape(text), len(text), now)
        self.call("/api/sms/send-sms", body)
        for _ in range(10):                       # 발신 결과(보통 몇 초)
            time.sleep(2)
            s = self.call("/api/sms/send-status")
            if re.search(r"<FailPhone>\S+</FailPhone>", s): return False
            if re.search(r"<SucPhone>\S+</SucPhone>", s): return True
        return None                                # 모름(통신사로는 넘어갔을 수 있음)


# ── 상태 파일 ─────────────────────────────────────────────────────
def load_state():
    try: return json.load(open(STATE, encoding="utf-8"))
    except Exception: return {}


def save_state(st):
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f: json.dump(st, f, ensure_ascii=False)
    os.replace(tmp, STATE)


def send_sms(api, st, cfg, phone, text, now, why):
    """상한 안이면 보내고 이번 달 조각 수를 센다 → True/False."""
    n = segments(text)
    if not can_send(st, now, n, cfg["SMS_MONTH_MAX"]):
        m = month_key(now)
        log("문자 상한(%d) 도달 — 안 보냄(%s): %s" % (cfg["SMS_MONTH_MAX"], why, text[:40]))
        if st.get("cap_warned") != m:
            st["cap_warned"] = m
            tg_send("📵 이번 달 문자 발송 상한 %d조각에 도달해 문자 답장·경보를 멈췄습니다(sms.env SMS_MONTH_MAX)."
                    % cfg["SMS_MONTH_MAX"])
        return False
    try:
        ok = api.send(phone, text)
    except Exception as e:
        log("문자 발신 실패(%s → %s): %s" % (why, mask_phone(phone), e)); return False
    sent = st.setdefault("sent", {}); m = month_key(now)
    sent[m] = sent.get(m, 0) + n
    log("문자 발신 %s(%s → %s, %d조각, 이번 달 %d): %s"
        % ("성공" if ok else ("결과 모름" if ok is None else "실패"), why, mask_phone(phone), n, sent[m], text[:60]))
    return ok is not False


def tg_send(text):
    try:
        import telegram_notify as tg
        tg.send(text)
    except Exception as e:
        log("텔레그램 기록 실패: %s" % e)


def _load(name):
    try: return json.load(open(os.path.join(BASE, name), encoding="utf-8"))
    except Exception: return {}


MODE_KR = {"frost": "동파", "comfort": "난방", "sleep": "취침"}


def short_status(state, status, latest, now):
    """단문(40자) 상태: '동파 1층13.6 외8.4 히터끔 도착11/20 19시'."""
    parts = [MODE_KR.get((status or {}).get("mode") or (state or {}).get("mode"), "?")]
    for label, name in (("1층", "내부1층"), ("외", "바닥외부")):
        v = (latest or {}).get(name) or {}
        if v.get("temp") is not None and now - v.get("time_ms", 0) / 1000.0 < 3600:
            parts.append("%s%.1f" % (label, v["temp"]))
    if status:
        parts.append(("히터%.0fW" % (status.get("watt") or 0)) if status.get("relay") else "히터끔")
    arr = (state or {}).get("arrival") or {}
    if arr.get("ts") and arr["ts"] > now:
        t = time.localtime(arr["ts"])
        parts.append("도착%d/%d %d시" % (t.tm_mon, t.tm_mday, t.tm_hour))
        st = state or {}
        if st.get("mode") == "comfort" and st.get("source") in ("arrival", "preheat") and st.get("since"):
            parts.append("예열중")
    return " ".join(parts)


def run_command(cmd):
    import telegram_bot as tb
    return tb.execute(tb.normalize(cmd))


def sms_reply(cmd, full, n, now=None):
    """문자 답장: 상태는 짧은 전용 문구, 나머지는 결과를 n글자로 줄임."""
    import telegram_bot as tb
    if tb.normalize(cmd).split(" ", 1)[0] in ("상태", "현황"):
        try:
            return compact(short_status(_load("heater_state.json"), _load("heater_status.json"),
                                        _load("latest.json"), now or time.time()), n)
        except Exception:
            pass
    return compact(full, n)


# ── 한 번 처리 ────────────────────────────────────────────────────
def process(api, cfg, st, now, runner=run_command, tg=tg_send, reply_fn=None):
    """문자함 처리 → 처리한 명령 목록."""
    done = []
    reply_fn = reply_fn or (lambda c, full, n: sms_reply(c, full, n, now))
    for m in api.inbox():
        if not m["unread"]: continue
        try: api.set_read(m["index"])
        except Exception as e: log("읽음 처리 실패 %s: %s" % (m["index"], e))
        phone = norm_phone(m["phone"])
        if phone not in cfg["allowed"]:
            log("허용 안 된 번호 무시: %s %s (%d자)" % (mask_phone(phone), m["date"], len(m["content"]))); continue
        cmd = parse_command(m["content"], cfg["SMS_PREFIX"])
        if cmd is None:
            log("비밀 단어 없는 문자 무시: %s %s" % (mask_phone(phone), m["date"])); continue
        import telegram_bot as tb
        verb = tb.normalize(cmd).split(" ", 1)[0]
        ts = parse_date(m["date"]) or now
        age = (now - ts) / 60.0
        if age > cfg["SMS_TTL_MIN"] and verb not in ALWAYS_OK:
            reply = "만료: %.0f분 전 '%s'는 실행 안 함. 다시 보내세요." % (age, cmd)
        else:
            try: reply = runner(cmd)
            except Exception as e: reply = "오류: %s" % e
        log("문자 명령 %s %r → %s" % (mask_phone(phone), cmd, reply.replace("\n", " | ")[:200]))
        send_sms(api, st, cfg, m["phone"], reply_fn(cmd, reply, cfg["SMS_REPLY_MAX"]), now, "답장")
        tg("📱 문자 명령 '%s'\n%s" % (cmd, reply))
        try: api.delete(m["index"])
        except Exception as e: log("삭제 실패 %s: %s" % (m["index"], e))
        done.append(cmd)
    return done


def telegram_backlog_min(now, queue_path=None):
    """텔레그램 대기열에서 가장 오래 못 나간 메시지의 나이(분). 없으면 0."""
    try:
        import telegram_notify as tgm
        path = queue_path or tgm.QUEUE
        ts = [json.loads(ln).get("ts") for ln in open(path, encoding="utf-8") if ln.strip()]
        ts = [t for t in ts if t]
        return (now - min(ts)) / 60.0 if ts else 0.0
    except Exception:
        return 0.0


def alarm_messages(decisions):
    return [(k, decisions[k][1]) for k in ALARM_KEYS if k in decisions and decisions[k][0]]


def alarm_step(api, cfg, st, now, backlog_min, decisions):
    """인터넷 끊김(텔레그램 대기열 정체) 중 켜진 중요 경보를 문자로 → 보낸 키 목록."""
    if not cfg["SMS_ALARM"] or backlog_min < cfg["SMS_ALARM_AFTER_MIN"] or not cfg["allowed"]: return []
    last = st.setdefault("alarm_last", {}); out = []
    to = sorted(cfg["allowed"])[0]
    for k, msg in alarm_messages(decisions):
        if now - last.get(k, 0) < cfg["SMS_ALARM_REPEAT_H"] * 3600: continue
        text = compact("[농막경보] " + re.sub(r"\s*\(.*$|\s*·.*$", "", msg), cfg["SMS_REPLY_MAX"])
        if send_sms(api, st, cfg, to, text, now, "경보 " + k):
            last[k] = now; out.append(k)
    return out


def run_once(now=None):
    now = now or time.time()
    cfg = config()
    if not cfg["allowed"] or not cfg["SMS_PREFIX"]:
        log("sms.env에 SMS_ALLOWED·SMS_PREFIX가 없음 — 아무것도 안 함"); return
    with open(LOCK, "a") as lk:
        try: fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return             # 앞 실행(예: 점검 1~2분)이 아직 도는 중
        st = load_state(); api = Hilink()
        try:
            process(api, cfg, st, now)
        except Exception as e:
            log("문자함 처리 실패: %s" % e)
        try:
            bl = telegram_backlog_min(now)
            if bl >= cfg["SMS_ALARM_AFTER_MIN"]:
                import cabin_watch as cw
                c, s, status, latest, fc, fcm = cw.gather()
                alarm_step(api, cfg, st, now, bl, cw.evaluate(now, c, s, status, latest, fc, fcm))
        except Exception as e:
            log("경보 확인 실패: %s" % e)
        save_state(st)


def show_status():
    cfg = config(); st = load_state(); now = time.time()
    print("허용 번호:", ", ".join(mask_phone(p) for p in sorted(cfg["allowed"])) or "(없음)")
    print("비밀 단어:", "설정됨(%d자)" % len(cfg["SMS_PREFIX"]) if cfg["SMS_PREFIX"] else "(없음)")
    print("이번 달 발송: %d / 상한 %d조각" % (st.get("sent", {}).get(month_key(now), 0), cfg["SMS_MONTH_MAX"]))
    print("경보 문자:", "켬(텔레그램 %d분 정체 시)" % cfg["SMS_ALARM_AFTER_MIN"] if cfg["SMS_ALARM"] else "끔")
    try:
        msgs = Hilink().inbox()
        print("문자함: %d건(안 읽음 %d)" % (len(msgs), sum(m["unread"] for m in msgs)))
    except Exception as e:
        print("문자함 읽기 실패:", e)


def main(argv):
    if len(argv) > 1 and argv[1] == "status": show_status(); return 0
    if len(argv) > 1 and argv[1] == "test-send":
        cfg = config(); st = load_state()
        if not cfg["allowed"]: print("SMS_ALLOWED 없음"); return 1
        ok = send_sms(Hilink(), st, cfg, sorted(cfg["allowed"])[0],
                      "[농막 Pi] 문자 통로 시험 " + time.strftime("%H:%M"), time.time(), "시험")
        save_state(st); return 0 if ok else 1
    run_once(); return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
