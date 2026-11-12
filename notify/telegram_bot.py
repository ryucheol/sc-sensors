#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""텔레그램 수신기(봇): 허락된 사람(@ryucheol)이 보낸 명령을 Pi에서 실행하고 결과를 답장. 설정: docs/TELEGRAM.ko.md

- 롱폴링(getUpdates)이라 Pi 쪽에 열린 포트·고정 IP가 필요 없다(핫스팟·LTE 그대로).
- **허락된 숫자 id만** 받는다(telegram.env 의 TELEGRAM_CHAT_ID, 추가로 TELEGRAM_ALLOWED_IDS=1,2).
  대화방 id와 보낸 사람 id가 둘 다 허락 목록에 있어야 한다(그룹에서 쓰려면 그룹 id와 내 id를 모두 넣음).
  사용자 이름(@ryucheol)은 바뀔 수 있어 인증에 쓰지 않고, 처음 한 번 `bind ryucheol` 로 숫자 id를 찾아 저장한다.
  허락되지 않은 사람에게는 답하지 않고 로그만 남긴다.
- 명령은 구글 시트 '명령' 탭과 똑같다(heater/sheet_cmd_poll.run 재사용):
    난방 [ON] [OFF] · 동파 [ON] · 취침 [ON] · 도착 HH:MM [내일|날짜] [목표°C] · 도착 취소 · 상태 · 점검 · 기상
  추가: 경보(지금 켜진 경보) · 도움.  /status /heat /frost /sleep /arrive /check /weather /alarms /help 도 된다.
- 오프라인 동안 쌓인 명령은 연결 뒤 한꺼번에 오는데, CMD_TTL_MIN(60분) 넘은 제어 명령은 실행하지 않고 '만료'로 답한다
  (상태·경보·도움은 언제든 답함).

실행: python3 notify/telegram_bot.py              # 상시 수신(systemd telegram-bot.service)
      python3 notify/telegram_bot.py bind ryucheol # 봇에게 말을 건 @ryucheol 의 chat_id를 telegram.env에 저장
"""
import os, sys, json, time, fcntl, re

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.join(REPO, "heater"))
import telegram_notify as tg   # noqa: E402

BASE = tg.BASE
OFFSET = os.path.join(BASE, "telegram_bot_offset.json")
LOG = os.path.join(BASE, "telegram_bot.log")
CMD_LOCK = os.path.join(REPO, ".sheet_cmd.lock")      # 시트 명령 폴러와 같은 잠금 파일(동시 실행 방지)
CMD_TTL_MIN = 60
POLL_TIMEOUT = 50                                     # 롱폴링 대기(초)
ALWAYS_OK = ("상태", "경보", "도움")                   # 오래된 메시지여도 답하는 명령

SLASH = {"start": "도움", "help": "도움", "status": "상태", "heat": "난방", "frost": "동파", "sleep": "취침",
         "arrive": "도착", "check": "점검", "weather": "기상", "alarms": "경보", "alarm": "경보"}
WORDS = {"도움": "도움", "도움말": "도움", "help": "도움", "?": "도움", "경보": "경보", "알람": "경보"}
BOT_COMMANDS = [("status", "지금 상태"), ("alarms", "켜진 경보"), ("heat", "난방 [ON] [OFF]"),
                ("frost", "동파 [ON]"), ("sleep", "취침 [ON]"), ("arrive", "도착 HH:MM [내일] / 도착 취소"),
                ("check", "히터 점검"), ("weather", "기상청 → 시트 갱신"), ("help", "도움말")]

HELP = """🏠 동지집 봇 명령 (시트 '명령' 탭과 같음)
• 상태 — 모드·온도·히터·전력
• 경보 — 지금 켜진 경보(과열·히터 오류·동파)
• 난방 / 난방 25 / 난방 25 27 — 난방 모드(1층 기준)
• 동파 / 동파 7 — 동파 모드
• 취침 / 취침 19 — 취침 모드(다락 기준)
• 도착 16:00 / 도착 내일 07:00 / 도착 16:00 24 / 도착 취소
• 점검 — 히터 자가점검(1~2분)
• 기상 — 기상청 → 시트 즉시 갱신
/로 시작해도 됩니다: /status /heat 25 /arrive 16:00"""


def log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f: f.write(line + "\n")
    except Exception:
        pass


def allowed_ids():
    _, cid = tg.config()
    extra = os.environ.get("TELEGRAM_ALLOWED_IDS") or tg._read_env_file().get("TELEGRAM_ALLOWED_IDS", "")
    ids = set()
    for x in [cid] + extra.split(","):
        x = (x or "").strip()
        if re.fullmatch(r"-?\d+", x): ids.add(int(x))
    return ids


def normalize(text):
    """'/heat@MyBot 25' → '난방 25', '상태' → '상태'. 빈 문자열이면 ''."""
    t = (text or "").strip()
    if t.startswith("/"):
        head, _, rest = t[1:].partition(" ")
        head = head.split("@", 1)[0].lower()
        t = (SLASH.get(head, head) + " " + rest).strip()
    first, _, rest = t.partition(" ")
    if first.lower() in WORDS: t = (WORDS[first.lower()] + " " + rest).strip()
    return t


def alarms_text():
    try:
        import cabin_watch as cw
        cfg, state, status, latest, fc, fc_mtime = cw.gather()
        dec = cw.evaluate(time.time(), cfg, state, status, latest, fc, fc_mtime)
        on = [dec[k][1] for k in cw.ALARMS if k in dec and dec[k][0]]
        return "\n".join(on) if on else "✅ 켜진 경보 없음"
    except Exception as e:
        return "경보 확인 실패: %s" % e


def execute(cmd):
    """명령 실행 → 답장 문구. 히터 명령은 시트 폴러와 같은 잠금 안에서."""
    verb = cmd.split(" ", 1)[0]
    if verb == "도움": return HELP
    if verb == "경보": return alarms_text()
    import sheet_cmd_poll as scp
    with open(CMD_LOCK, "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            status, result = scp.run(cmd)
        except Exception as e:
            status, result = "오류", str(e)
    return ("✅ " if status == "완료" else "⚠ ") + result


def handle(update, allowed, now, runner=execute):
    """update 하나 → (chat_id, 답장) 또는 None(무시). 순수에 가깝게: 실행은 runner."""
    m = update.get("message") or update.get("edited_message")
    if not m: return None
    chat = (m.get("chat") or {}).get("id"); frm = (m.get("from") or {})
    if chat not in allowed or frm.get("id") not in allowed:      # 대화방과 보낸 사람 둘 다 허락돼야 함
        log("허락되지 않은 발신 무시: chat=%s from=%s @%s 내용=%r"
            % (chat, frm.get("id"), frm.get("username", ""), (m.get("text") or "")[:40]))
        return None
    text = m.get("text")
    if not text: return chat, "글자로 된 명령만 받습니다. '도움'을 보내 보세요."
    cmd = normalize(text)
    if not cmd: return chat, HELP
    age_min = (now - m.get("date", now)) / 60.0
    if age_min > CMD_TTL_MIN and cmd.split(" ", 1)[0] not in ALWAYS_OK:
        return chat, "⏰ 만료: %.0f분 전에 보낸 '%s'는 실행하지 않았습니다(Pi 오프라인이었음). 필요하면 다시 보내 주세요." % (age_min, cmd)
    reply = runner(cmd)
    log("명령 %r → %s" % (cmd, reply.replace("\n", " | ")[:200]))
    return chat, reply


# ── 롱폴링 루프 ──────────────────────────────────────────────────

def load_offset():
    try:
        with open(OFFSET) as f: return int(json.load(f).get("offset", 0))
    except Exception:
        return 0


def save_offset(off):
    tmp = OFFSET + ".tmp"
    with open(tmp, "w") as f: json.dump({"offset": off}, f)
    os.replace(tmp, OFFSET)


def poll_once(tok, allowed, offset, urlopen=None, runner=execute, timeout=POLL_TIMEOUT):
    """getUpdates 한 번 → 새 offset. 각 update는 처리 직후 offset을 저장(재시작 시 중복 실행 방지)."""
    ups = tg._call(tok, "getUpdates", {"offset": offset, "timeout": timeout,
                                       "allowed_updates": json.dumps(["message", "edited_message"])},
                   urlopen, timeout=timeout + 15) or []
    for u in ups:
        offset = u["update_id"] + 1
        save_offset(offset)                # 실행 '전에' 저장: 명령이 죽거나 재시작돼도 두 번 실행하지 않음
        try:
            r = handle(u, allowed, time.time(), runner)
        except Exception as e:
            log("처리 오류: %s" % e); r = None
        if r:
            try: tg._send_now(tok, r[0], r[1], urlopen=urlopen)
            except Exception as e: log("답장 실패: %s" % tg._mask(e, tok))
    return offset


def run_forever():
    tok, _ = tg.config()
    allowed = allowed_ids()
    if not tok or not allowed:
        log("telegram.env에 TELEGRAM_BOT_TOKEN·TELEGRAM_CHAT_ID 필요 — `bind <사용자이름>` 먼저"); return 1
    try:
        tg._call(tok, "deleteWebhook", {})            # 웹훅이 걸려 있으면 getUpdates가 409로 막힘
        tg._call(tok, "setMyCommands", {"commands": json.dumps(
            [{"command": c, "description": d} for c, d in BOT_COMMANDS], ensure_ascii=False)})
    except Exception as e:
        log("시작 설정 실패(계속 진행): %s" % tg._mask(e, tok))
    log("수신 시작(허락 %d명)" % len(allowed))
    tg.send("🟢 동지집 Pi 봇 시작 (%s) — '도움'으로 명령 보기" % time.strftime("%m/%d %H:%M"),
            silent=True, key="bot_start", min_interval=3600, queue=False)
    offset = load_offset(); backoff = 5
    while True:
        try:
            offset = poll_once(tok, allowed, offset)
            backoff = 5
        except Exception as e:
            msg = tg._mask(e, tok)
            if "409" in msg: log("다른 곳에서 같은 봇을 수신 중(409) — 하나만 실행할 것")
            elif "연결 실패" not in msg: log("수신 오류: %s" % msg)
            time.sleep(backoff); backoff = min(backoff * 2, 120)


def bind(username, urlopen=None):
    """봇에게 말을 건 @username 의 private chat id를 telegram.env에 저장."""
    tok, _ = tg.config()
    if not tok: print("telegram.env에 TELEGRAM_BOT_TOKEN을 먼저 넣으세요"); return 1
    want = username.lstrip("@").lower()
    found = None
    for u in tg._call(tok, "getUpdates", {"timeout": "0"}, urlopen) or []:
        m = u.get("message") or {}
        c, f = m.get("chat") or {}, m.get("from") or {}
        if c.get("type") == "private" and (f.get("username") or "").lower() == want:
            found = c["id"]
    if not found:
        print("@%s 의 메시지를 찾지 못했습니다 — 텔레그램에서 봇에게 아무 말이나 보낸 뒤 다시 실행하세요" % want); return 1
    lines = []
    try:
        with open(tg.ENV_FILE, encoding="utf-8") as f: lines = f.read().splitlines()
    except FileNotFoundError:
        pass
    lines = [l for l in lines if not l.strip().startswith("TELEGRAM_CHAT_ID=")] + ["TELEGRAM_CHAT_ID=%s" % found]
    tmp = tg.ENV_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f: f.write("\n".join(lines) + "\n")
    os.chmod(tmp, 0o600); os.replace(tmp, tg.ENV_FILE)
    print("@%s → TELEGRAM_CHAT_ID=%s 저장(%s)" % (want, found, tg.ENV_FILE)); return 0


def main(argv):
    if len(argv) >= 3 and argv[1] == "bind": return bind(argv[2])
    if len(argv) >= 2 and argv[1] in ("-h", "--help"): print(__doc__); return 0
    return run_forever() or 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
