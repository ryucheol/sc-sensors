#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pi → 텔레그램 메시지 보내기 (표준 라이브러리만 사용). 설정·설치: docs/TELEGRAM.ko.md

설정: ~/smart-cabin/telegram.env (또는 같은 이름의 환경변수가 우선)
    TELEGRAM_BOT_TOKEN=123456:ABC...      # @BotFather 가 준 토큰 (비밀, 커밋 금지)
    TELEGRAM_CHAT_ID=123456789            # 받을 사람/그룹 (getid 로 확인)

다른 프로그램에서:
    sys.path.insert(0, os.path.expanduser("~/smart-cabin/notify"))
    import telegram_notify as tg
    tg.send("동파 주의: 바깥 -8°C")                       # 실패해도 예외 없음. 큐에 넣고 False
    tg.send("플러그 통신 끊김", key="plug_down", min_interval=1800)   # 같은 key는 30분에 한 번
    tg.send("새벽 알림", silent=True)                      # 무음 알림

CLI:
    python3 notify/telegram_notify.py send "문장"   # 보내기(실패 시 큐 보관, 종료코드 1)
    echo "여러 줄" | python3 notify/telegram_notify.py send -
    python3 notify/telegram_notify.py flush          # 큐에 쌓인 메시지 재전송(디스패처가 연결 때 호출)
    python3 notify/telegram_notify.py test           # 설정 확인 + 시험 메시지
    python3 notify/telegram_notify.py getid          # 봇에게 말 건 사람/그룹의 chat_id 출력

원칙: 알림 때문에 히터·센서 프로그램이 멈추면 안 된다 → send()는 절대 예외를 던지지 않는다.
      토큰은 로그·오류 메시지에 찍지 않는다(***로 가림).
"""
import os, sys, json, time, fcntl, urllib.request, urllib.parse, urllib.error

BASE = os.path.expanduser(os.environ.get("SMART_CABIN_DIR", "~/smart-cabin"))
ENV_FILE = os.path.join(BASE, "telegram.env")
QUEUE = os.path.join(BASE, "telegram_queue.jsonl")
STATE = os.path.join(BASE, "telegram_state.json")      # key별 마지막 전송 시각(중복 억제)
LOG = os.path.join(BASE, "telegram.log")
API = "https://api.telegram.org/bot%s/%s"
MAX_LEN = 4096          # 텔레그램 한 메시지 최대 글자 수
QUEUE_MAX = 200         # 오래 오프라인이어도 큐는 최근 이만큼만
QUEUE_MAX_AGE = 3 * 86400   # 3일 넘은 메시지는 버림(지난 경보는 의미 없음)
TIMEOUT = 15


# ── 설정 ─────────────────────────────────────────────────────────

def _read_env_file(path=None):
    out = {}
    try:
        with open(path or ENV_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line: continue
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return out


def config():
    """→ (token, chat_id). 환경변수가 파일보다 우선. 없으면 None."""
    f = _read_env_file()
    tok = os.environ.get("TELEGRAM_BOT_TOKEN") or f.get("TELEGRAM_BOT_TOKEN") or None
    cid = os.environ.get("TELEGRAM_CHAT_ID") or f.get("TELEGRAM_CHAT_ID") or None
    return tok, cid


def _mask(s, tok):
    s = str(s)
    return s.replace(tok, "***") if tok else s


def log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        with open(LOG, "a", encoding="utf-8") as f: f.write(line + "\n")
    except Exception:
        pass
    return line


# ── API 호출 ─────────────────────────────────────────────────────

def _call(tok, method, params, urlopen=None, timeout=TIMEOUT):
    """텔레그램 API 호출 → result. 실패면 RuntimeError(토큰 가림). 429면 retry_after를 메시지에 담음."""
    urlopen = urlopen or urllib.request.urlopen
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(API % (tok, method), data=data)
    try:
        with urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try: body = json.loads(e.read().decode("utf-8", "replace"))
        except Exception: raise RuntimeError(_mask("HTTP %s" % e.code, tok))
    except Exception as e:
        raise RuntimeError(_mask("연결 실패: %s" % e, tok))
    if not body.get("ok"):
        ra = (body.get("parameters") or {}).get("retry_after")
        raise RuntimeError(_mask("API 오류 %s %s%s" % (body.get("error_code"), body.get("description", ""),
                                                      " (retry_after %ss)" % ra if ra else ""), tok))
    return body.get("result")


def _split(text, n=MAX_LEN):
    text = str(text)
    if len(text) <= n: return [text]
    parts = []
    while text:
        cut = text.rfind("\n", 0, n)
        if cut <= 0: cut = n
        parts.append(text[:cut]); text = text[cut:].lstrip("\n")
    return parts


def _send_now(tok, cid, text, silent=False, urlopen=None):
    for part in _split(text):
        _call(tok, "sendMessage", {"chat_id": cid, "text": part,
                                   "disable_notification": "true" if silent else "false",
                                   "disable_web_page_preview": "true"}, urlopen)


# ── 큐 · 중복 억제 ───────────────────────────────────────────────

class _Lock:
    """큐·상태 파일 동시 접근 보호(히터·알림·kma_sync가 동시에 보낼 수 있음)."""
    def __enter__(self):
        self.f = open(os.path.join(BASE, ".telegram.lock"), "a")
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self
    def __exit__(self, *a):
        try: fcntl.flock(self.f, fcntl.LOCK_UN); self.f.close()
        except Exception: pass


def _load_queue():
    out = []
    try:
        with open(QUEUE, encoding="utf-8") as f:
            for line in f:
                try: out.append(json.loads(line))
                except Exception: pass
    except Exception:
        pass
    return out


def _save_queue(items):
    tmp = QUEUE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for it in items[-QUEUE_MAX:]: f.write(json.dumps(it, ensure_ascii=False) + "\n")
    os.replace(tmp, QUEUE)


def _load_state():
    try:
        with open(STATE, encoding="utf-8") as f: return json.load(f)
    except Exception:
        return {}


def _save_state(st):
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f: json.dump(st, f)
    os.replace(tmp, STATE)


def _stamp(ts):
    return time.strftime("%m/%d %H:%M", time.localtime(ts))


def flush(urlopen=None, now=None):
    """큐 재전송 → (보낸 수, 남은 수). 오래된 것은 버림. 실패하면 거기서 멈추고 나머지 보존(순서 유지)."""
    tok, cid = config()
    if not tok or not cid: return 0, len(_load_queue())
    now = now or time.time()
    with _Lock():
        q = [it for it in _load_queue() if now - it.get("ts", now) <= QUEUE_MAX_AGE]
        sent = 0
        while q:
            it = q[0]
            text = "[지연 %s] %s" % (_stamp(it["ts"]), it["text"])
            try:
                _send_now(tok, cid, text, it.get("silent", False), urlopen)
            except Exception as e:
                log("큐 재전송 실패(남은 %d): %s" % (len(q), _mask(e, tok)))
                break
            q.pop(0); sent += 1
        _save_queue(q)
    if sent: log("큐 재전송 %d건" % sent)
    return sent, len(q)


def send(text, silent=False, key=None, min_interval=0, queue=True, urlopen=None, now=None):
    """메시지 보내기 → 바로 보냈으면 True. 예외를 던지지 않는다.
    key + min_interval: 같은 key는 min_interval초 안에 다시 보내지 않음(억제되면 False, 큐에도 안 넣음).
    queue=False: 실패해도 큐에 넣지 않음(지금만 의미 있는 메시지)."""
    try:
        now = now or time.time()
        tok, cid = config()
        if key and min_interval:
            with _Lock():
                st = _load_state()
                if key in st and now - st[key] < min_interval: return False
                st[key] = now; _save_state(st)
        if not tok or not cid:
            log("설정 없음(telegram.env) — 보내지 못함: %s" % str(text)[:60])
            if queue: _enqueue(text, silent, now)
            return False
        if _load_queue():
            flush(urlopen, now)              # 밀린 것부터(순서 유지)
        try:
            _send_now(tok, cid, text, silent, urlopen)
            return True
        except Exception as e:
            log("전송 실패%s: %s" % (" → 큐 보관" if queue else "", _mask(e, tok)))
            if queue: _enqueue(text, silent, now)
            return False
    except Exception as e:                    # 무엇이 잘못돼도 호출한 프로그램은 계속 돈다
        try: log("send 내부 오류: %s" % _mask(e, config()[0]))
        except Exception: pass
        return False


def _enqueue(text, silent, now):
    with _Lock():
        q = _load_queue()
        q.append({"ts": now, "text": str(text), "silent": bool(silent)})
        _save_queue(q)


def get_chat_ids(urlopen=None):
    """봇에게 최근 말 건 대화 목록 → [(chat_id, 이름/제목, 종류)]."""
    tok, _ = config()
    if not tok: raise RuntimeError("TELEGRAM_BOT_TOKEN 없음(telegram.env)")
    seen = {}
    for u in _call(tok, "getUpdates", {"timeout": "0"}, urlopen) or []:
        m = u.get("message") or u.get("channel_post") or u.get("my_chat_member") or {}
        c = m.get("chat") or {}
        if "id" in c:
            name = c.get("title") or " ".join(x for x in (c.get("first_name"), c.get("last_name")) if x) or c.get("username", "")
            if c.get("username"): name += " @%s" % c["username"]
            seen[c["id"]] = (c["id"], name.strip(), c.get("type", ""))
    return list(seen.values())


# ── CLI ──────────────────────────────────────────────────────────

def main(argv):
    if len(argv) < 2 or argv[1] not in ("send", "flush", "test", "getid"):
        print(__doc__); return 2
    cmd = argv[1]
    if cmd == "send":
        text = sys.stdin.read() if len(argv) < 3 or argv[2] == "-" else " ".join(argv[2:])
        if not text.strip(): print("보낼 내용이 없습니다"); return 2
        ok = send(text.strip(), silent="--silent" in argv)
        print("보냄" if ok else "보내지 못함(큐 보관 — telegram.log 확인)")
        return 0 if ok else 1
    if cmd == "flush":
        sent, left = flush()
        print("재전송 %d건, 남은 %d건" % (sent, left)); return 0 if left == 0 else 1
    if cmd == "getid":
        try:
            rows = get_chat_ids()
        except Exception as e:
            print("실패:", _mask(e, config()[0])); return 1
        if not rows: print("기록 없음 — 텔레그램에서 봇에게 아무 말이나 보낸 뒤 다시 실행하세요"); return 1
        for cid, name, kind in rows: print("TELEGRAM_CHAT_ID=%s   # %s (%s)" % (cid, name, kind))
        return 0
    # test
    tok, cid = config()
    print("토큰:", "있음" if tok else "없음", "/ chat_id:", cid or "없음", "/ 설정 파일:", ENV_FILE)
    if not tok or not cid: return 1
    try:
        me = _call(tok, "getMe", {})
        print("봇: @%s" % me.get("username"))
        _send_now(tok, cid, "✅ smart-cabin 텔레그램 시험 %s" % time.strftime("%Y-%m-%d %H:%M"))
        print("시험 메시지 보냄"); return 0
    except Exception as e:
        print("실패:", _mask(e, tok)); return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
